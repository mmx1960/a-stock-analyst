from __future__ import annotations

import argparse, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb, pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data.market_data_provider import DuckDBMarketDataProvider
from backtest.evaluation.hold_return import evaluate_signal_hold_return, summarize_evaluated_trades
from backtest.filters.no_touch_filters import check_no_touch_filters
from backtest.strategies.kaipanla_sector_strength_score import score_sector_strength_safe
from backtest.strategies.strategy_d_shen_trend_pullback import *
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import INTRADAY_MA, MIN_MINUTE_BARS
from backtest.workflows.selection_workflow import check_sector_strength_top_n, workflow_final_score


def jd(v):
    if isinstance(v, (pd.Timestamp, datetime)): return v.isoformat()
    if hasattr(v, 'item'): return v.item()
    return str(v)

def nd(s):
    s=str(s); return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s)==8 and s.isdigit() else s[:10]

def sf(v,d=0.0):
    try:
        if v is None or pd.isna(v): return d
        return float(v)
    except Exception: return d

def prepare_daily(df):
    if df.empty: return df
    f=df.copy(); f['trade_date']=pd.to_datetime(f['trade_date'])
    for c in ['open','high','low','close','volume','amount']: f[c]=pd.to_numeric(f[c],errors='coerce')
    f['ma20']=f['close'].rolling(DAILY_MA_FAST).mean(); f['ma30']=f['close'].rolling(DAILY_MA_MID).mean(); f['ma60']=f['close'].rolling(DAILY_MA_SLOW).mean(); f['ma120']=f['close'].rolling(DAILY_MA_LONG,min_periods=60).mean()
    f['prev_ma20_5']=f['ma20'].shift(5); f['prev_ma60_20']=f['ma60'].shift(20)
    f['recent_high_120']=f['high'].rolling(120,min_periods=1).max(); f['recent_low_60']=f['low'].rolling(60,min_periods=1).min(); f['avg20_amount']=f['amount'].rolling(20,min_periods=1).mean(); f['daily_bars']=range(1,len(f)+1)
    return f

def daily_ok(r):
    if int(r.get('daily_bars',0)) < MIN_DAILY_BARS: return None
    close,ma20,ma30,ma60,ma120=map(sf,[r.get('close'),r.get('ma20'),r.get('ma30'),r.get('ma60'),r.get('ma120')])
    if min(close,ma20,ma30,ma60)<=0: return None
    alignment=close>ma20>ma30>ma60; s20=ma20>=sf(r.get('prev_ma20_5')) if sf(r.get('prev_ma20_5'))>0 else True; s60=ma60>=sf(r.get('prev_ma60_20')) if sf(r.get('prev_ma60_20'))>0 else True; ma120ok=close>=ma120 if ma120>0 else True
    hi=sf(r.get('recent_high_120')); lo=sf(r.get('recent_low_60')); space=(hi/close-1)*100 if close>0 and hi>0 else 0; dd=close>=lo*1.08 if lo>0 else True; ext=(close/ma20-1)*100 if ma20>0 else 999; notext=ext<=MAX_DAILY_EXTENSION_FROM_MA20_PCT
    amt=sf(r.get('amount')); avg=sf(r.get('avg20_amount')); liq=amt>=MIN_LATEST_DAILY_AMOUNT and avg>=MIN_AVG20_DAILY_AMOUNT
    score=35+(18 if alignment else 0)+(12 if s20 else 0)+(10 if s60 else 0)+(8 if ma120ok else 0)+min(10,max(0,space/2))+(7 if dd else 0)+(5 if notext else -8)+(6 if liq else -15)
    score=round(max(0,min(100,score)),2)
    if not (alignment and s20 and s60 and ma120ok and dd and notext and liq and score>=MIN_DAILY_TREND_SCORE): return None
    return {'d_shen_context_score':score,'daily_close':round(close,4),'daily_ma20':round(ma20,4),'daily_extension_from_ma20_pct':round(ext,4),'daily_latest_amount':round(amt,2),'daily_avg20_amount':round(avg,2)}

def prepare_minute(df):
    if df.empty: return df
    f=df.copy(); f['trade_dt']=pd.to_datetime(f['trade_dt'])
    for c in ['open','high','low','close','volume','amount']: f[c]=pd.to_numeric(f[c],errors='coerce')
    f['ma5']=f['close'].rolling(5).mean(); f['ma13']=f['close'].rolling(INTRADAY_MA).mean(); f['ma30']=f['close'].rolling(30).mean(); f['prev_ma13']=f['ma13'].shift(1)
    mn=f[['ma5','ma13','ma30']].min(axis=1); mx=f[['ma5','ma13','ma30']].max(axis=1); f['ma_convergence_pct']=(mx/mn-1)*100; f['ma_convergence_score']=(100-f['ma_convergence_pct']*18).clip(lower=0)
    f['signal_ok']=(f['low']<=f['ma13']*(1+MAX_30M_PULLBACK_TO_MA13_PCT))&(f['close']>=f['ma13'])&(f['close']<=f['ma13']*(1+MAX_30M_CLOSE_ABOVE_MA13_PCT))&(f['ma13']>=f['prev_ma13'])&(f['close']>=f['open'])&(f['close']>=(f['low']+(f['high']-f['low'])*0.55))&(f['ma_convergence_score']>=MIN_30M_MA_CONVERGENCE_SCORE)&(f['ma5']>=f['ma13']*0.985)&(f['ma13']>=f['ma30']*0.985)
    f.loc[range(min(MIN_MINUTE_BARS-1,len(f))),'signal_ok']=False
    return f

def make_signal(mrow, trade_date, meta):
    dt=pd.to_datetime(mrow['trade_dt']); close=sf(mrow.get('close')); ma13=sf(mrow.get('ma13'))
    score=50+min(16,max(0,sf(mrow.get('ma_convergence_score'))/100*16))+12+10+8+ (min(4,max(0,(close/ma13-1)*200)) if ma13>0 else 0)
    return {**meta,'buy_date':str(dt.date()),'signal_time':dt.isoformat(),'signal_price':round(close,4),'signal_score':round(min(100,score),2),'signal_reason':'d_shen_trend_30m_pullback','structure_period':'30','structure_freq':'30分钟','ma_convergence_score':round(sf(mrow.get('ma_convergence_score')),2),'theme':'D神趋势回踩','theme_heat_score':DEFAULT_THEME_HEAT_SCORE,'strategy_version':'d-shen-trend-30m-pullback-v1','days_ago':max(0,(trade_date.normalize()-dt.normalize()).days)}

def enrich(sig, code, name, trade_date, min_sector, min_final, daily=None, require_sector_strength_top_n=10):
    scored=score_sector_strength_safe(code=code,buy_date=trade_date,sector_name=sig.get('theme'),lookback_trade_days=10)
    e={**sig,**scored,'strategy_id':'d_shen_trend_30m_pullback','strategy_name':'D神趋势 + 板块资金 + 30分钟回踩','code':code,'name':name}
    top_n_ok, top_n_meta = check_sector_strength_top_n(candidate_sectors=e.get('kaipanla_candidate_sectors') or [], trade_date=str(e.get('buy_date') or trade_date), top_n=require_sector_strength_top_n)
    e.update(top_n_meta)
    if not top_n_ok: return None
    no_touch_ok, no_touch_meta = check_no_touch_filters(code=code, buy_date=str(e.get('buy_date') or trade_date), daily=daily, enforce_sector_top3=False)
    e.update(no_touch_meta)
    if not no_touch_ok: return None
    final,bd=workflow_final_score(e); e['workflow_final_score']=final; e['workflow_score_breakdown']=bd
    if sf(e.get('kaipanla_strength_score')) < min_sector or final < min_final: return None
    return e

def run(a):
    start,end=nd(a.start_date),nd(a.end_date); preload=str((pd.to_datetime(start)-pd.Timedelta(days=a.signal_window_days+520)).date())
    provider=DuckDBMarketDataProvider(); candidates=defaultdict(list); raw=defaultdict(int); rej=defaultdict(int)
    with duckdb.connect(a.db_path, read_only=True) as con:
        dates=[pd.to_datetime(x) for x in con.execute("select distinct trade_date from daily_kline where adjust='hfq' and trade_date between ? and ? order by trade_date",[start,end]).df()['trade_date'].tolist()]
        stocks=con.execute(("select code,name from stock_basic order by code" + (f" limit {a.max_stocks}" if a.max_stocks>0 else ""))).df(); total=len(stocks)
        for idx,(_,st) in enumerate(stocks.iterrows(),1):
            code=str(st.get('code')).zfill(6); name=str(st.get('name') or code)
            if is_risky_stock_name(name): continue
            daily=prepare_daily(con.execute("select trade_date,open,high,low,close,volume,amount,turnover_rate,change_pct from daily_kline where code=? and adjust='hfq' and trade_date between ? and ? order by trade_date",[code,preload,end]).df())
            minute=prepare_minute(con.execute("select trade_dt,open,high,low,close,volume,amount from minute_kline where code=? and period='30' and trade_dt between ? and ? order by trade_dt",[code,f'{preload} 00:00:00',f'{end} 23:59:59']).df())
            if daily.empty or minute.empty: continue
            sigrows=minute[minute['signal_ok']]
            if sigrows.empty: continue
            for d in dates:
                dr=daily[daily['trade_date']<=d]
                if dr.empty: continue
                meta=daily_ok(dr.iloc[-1])
                if not meta: continue
                elig=sigrows[(sigrows['trade_dt']<=d+pd.Timedelta(hours=23,minutes=59,seconds=59))&(sigrows['trade_dt']>=d-pd.Timedelta(days=a.signal_window_days))]
                if elig.empty: continue
                key=str(d.date()); raw[key]+=1
                sig=make_signal(elig.iloc[-1],d,meta); e=enrich(sig,code,name,key,a.min_sector_score,a.min_final_score,daily=dr,require_sector_strength_top_n=a.require_sector_strength_top_n)
                if e: candidates[key].append(e)
                else: rej[key]+=1
            if idx%a.progress_every==0: print(f'[{idx}/{total}] candidates={sum(len(v) for v in candidates.values())}', flush=True)
    trades=[]; daily_results=[]
    for i,d in enumerate(dates,1):
        key=str(d.date()); selected=sorted(candidates[key], key=lambda x:(-sf(x.get('workflow_final_score')),-sf(x.get('kaipanla_strength_score')),-sf(x.get('signal_score')),x.get('code')))[:a.top_n]
        day=[]
        for e in selected:
            ev=evaluate_signal_hold_return(e,provider=provider,hold_days=a.hold_days,adjust='hfq')
            t={'strategy_id':e.get('strategy_id'),'strategy_name':e.get('strategy_name'),'code':e.get('code'),'name':e.get('name'),'buy_date':e.get('buy_date') or key,'backtest_trade_date':key,'signal_price':e.get('signal_price'),'signal_score':e.get('signal_score'),'theme':e.get('theme'),'structure_period':e.get('structure_period'),'workflow_final_score':e.get('workflow_final_score'),'score_snapshot':{'kaipanla_strength_score':e.get('kaipanla_strength_score'),'kaipanla_strength_grade':e.get('kaipanla_strength_grade'),'kaipanla_candidate_sectors':e.get('kaipanla_candidate_sectors'),'stock_sector_membership_count':e.get('stock_sector_membership_count')},**ev}
            day.append(t); trades.append(t)
        daily_results.append({'trade_date':key,'index':i,'workflow_counts':{'raw_signals':raw[key],'selected':len(selected),'rejected':rej[key]+max(0,len(candidates[key])-len(selected))},'selected_count':len(selected),'rejected_count':rej[key]+max(0,len(candidates[key])-len(selected)),'summary':summarize_evaluated_trades(day)})
    summ=summarize_evaluated_trades(trades)
    return {'backtest':'d-shen-vector-topn-h1-v1','generated_at':datetime.now().isoformat(timespec='seconds'),'config':vars(a),'trade_dates':[str(d.date()) for d in dates],'counts':{'trade_days':len(dates),'trades':len(trades),'evaluated':summ.get('evaluated',0),'data_missing':summ.get('data_missing',0)},'summary':summ,'daily_results':daily_results,'trades':trades}

def parse():
    p=argparse.ArgumentParser(); p.add_argument('--start-date',default='2025-01-01'); p.add_argument('--end-date',default='2025-06-30'); p.add_argument('--hold-days',type=int,default=10); p.add_argument('--max-stocks',type=int,default=5200); p.add_argument('--signal-window-days',type=int,default=10); p.add_argument('--min-sector-score',type=float,default=40); p.add_argument('--min-final-score',type=float,default=0); p.add_argument('--top-n',type=int,default=30); p.add_argument('--require-sector-strength-top-n',type=int,default=10); p.add_argument('--db-path',default='data/ashare.duckdb'); p.add_argument('--output',default='/tmp/d_shen_vector_topn_h1.json'); p.add_argument('--progress-every',type=int,default=200); return p.parse_args()

def main():
    a=parse(); out=run(a); path=Path(a.output); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=jd),encoding='utf-8'); print(path); print(json.dumps({'counts':out['counts'],'summary':out['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
