import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import plotly.graph_objects as go
import threading
import time
import os
try:
    import resend
    RESEND_OK = True
except:
    RESEND_OK = False
from datetime import datetime, timezone

st.set_page_config(page_title="SMC Signals Pro", page_icon="📈", layout="wide")
pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","AUDUSD=X","NZDUSD=X","USDCAD=X","GC=F"]

def obter_dados(par):
    d = yf.download(par, period="5d", interval="15m", auto_adjust=True)
    d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
    return d.dropna()

def adicionar_indicadores(df):
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["ema20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["Close"], window=50).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["Close"], window=200).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"],df["Low"],df["Close"],window=14).average_true_range()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["amplitude"] = df["High"]-df["Low"]
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","qualidade":"EXCELENTE","score_bonus":15,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","qualidade":"BOA","score_bonus":10,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","qualidade":"BOA","score_bonus":10,"operar":True}
    else: return {"sessao":"ASIATICA","qualidade":"FRACA","score_bonus":0,"operar":False}

def detectar_estrutura(df):
    df = df.copy()
    df["sh"] = ((df["High"]>df["High"].shift(1))&(df["High"]>df["High"].shift(-1))&(df["High"]>df["High"].shift(2))&(df["High"]>df["High"].shift(-2))&(df["High"]>df["High"].shift(3))&(df["High"]>df["High"].shift(-3)))
    df["sl"] = ((df["Low"]<df["Low"].shift(1))&(df["Low"]<df["Low"].shift(-1))&(df["Low"]<df["Low"].shift(2))&(df["Low"]<df["Low"].shift(-2))&(df["Low"]<df["Low"].shift(3))&(df["Low"]<df["Low"].shift(-3)))
    sh=df[df["sh"]]["High"]; sl=df[df["sl"]]["Low"]
    if len(sh)<3 or len(sl)<3:
        return {"bos_bull":False,"bos_bear":False,"choch":False,"tendencia":"NEUTRO","romp":False,"ush":0,"usl":0}
    ush=sh.iloc[-1]; psh=sh.iloc[-2]; usl=sl.iloc[-1]; psl=sl.iloc[-2]
    p=df["Close"].iloc[-1]; pp=df["Close"].iloc[-2]
    bb=(p>ush)and(pp>ush); bs=(p<usl)and(pp<usl)
    hh=ush>psh; hl=usl>psl; lh=ush<psh; ll=usl<psl
    t="BULLISH" if(hh and hl)else "BEARISH" if(lh and ll)else "NEUTRO"
    ch=((hh and hl)and bs)or((lh and ll)and bb)
    return {"bos_bull":bb,"bos_bear":bs,"choch":ch,"tendencia":t,"romp":bb or bs,"ush":round(float(ush),5),"usl":round(float(usl),5)}

def detectar_smc(df):
    df=df.copy(); df["vf"]=df["corpo"]>df["media_corpo"]*1.5
    ob=None; obs=None
    for i in range(len(df)-3,len(df)-25,-1):
        if df["vf"].iloc[i]:
            if df["Close"].iloc[i]<df["Open"].iloc[i]:
                if df["Close"].iloc[-1]>df["High"].iloc[i]: ob=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
            else:
                if df["Close"].iloc[-1]<df["Low"].iloc[i]: obs=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
    fb=False; fs=False
    for i in range(len(df)-2,len(df)-20,-1):
        if i<1 or i+1>=len(df): continue
        if df["Low"].iloc[i+1]>df["High"].iloc[i-1]: fb=True; break
        if df["High"].iloc[i+1]<df["Low"].iloc[i-1]: fs=True; break
    h=df["High"].tail(30); l=df["Low"].tail(30); p=df["Close"].iloc[-1]
    lt=round(float(h.max()),5); lf=round(float(l.min()),5)
    return {"ob_bull":ob,"ob_bear":obs,"fvg_bull":fb,"fvg_bear":fs,"lt":lt,"lf":lf,"ct":abs(p-lt)/lt<0.0008,"cf":abs(p-lf)/lf<0.0008}

def confirmar(df, est):
    p=df["Close"].iloc[-1]; rsi=df["rsi"].iloc[-1]
    e20=df["ema20"].iloc[-1]; e50=df["ema50"].iloc[-1]; e200=df["ema200"].iloc[-1]
    cb=[]; cs=[]
    if rsi<45: cs.append(f"RSI {round(rsi,1)}")
    elif rsi>55: cb.append(f"RSI {round(rsi,1)}")
    if p>e20: cb.append("Acima EMA20")
    else: cs.append("Abaixo EMA20")
    if p>e50: cb.append("Acima EMA50")
    else: cs.append("Abaixo EMA50")
    if p>e200: cb.append("Acima EMA200")
    else: cs.append("Abaixo EMA200")
    if e20>e50>e200: cb.append("EMAs Bull")
    elif e20<e50<e200: cs.append("EMAs Bear")
    if est["tendencia"]=="BULLISH": cb.append("Estrutura Bull")
    elif est["tendencia"]=="BEARISH": cs.append("Estrutura Bear")
    d="BUY" if len(cb)>=len(cs) else "SELL"
    c=cb if d=="BUY" else cs
    return {"dir":d,"forca":len(c),"conf":c,"bl_bull":rsi>75,"bl_bear":rsi<25,"rsi":round(rsi,1)}

def calc_risco(preco, d, atr, rsi):
    p=float(preco); a=float(atr); m=1.5 if(rsi>60 or rsi<40)else 2.0
    if d=="BUY":
        sl=round(p-a*m,5); tp1=round(p+a*2,5); tp2=round(p+a*4,5)
        if sl>=p: sl=round(p-a,5)
        if tp1<=p: tp1=round(p+a,5)
    else:
        sl=round(p+a*m,5); tp1=round(p-a*2,5); tp2=round(p-a*4,5)
        if sl<=p: sl=round(p+a,5)
        if tp1>=p: tp1=round(p-a,5)
    r=abs(p-sl)
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":round(r*10000,1),"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def analisar(par, ignorar_sessao=False):
    try:
        d=obter_dados(par)
        if len(d)<100: return None
        d=adicionar_indicadores(d)
        s=sessao_activa()
        if not ignorar_sessao and not s["operar"]: return None
        est=detectar_estrutura(d); smc=detectar_smc(d); t=confirmar(d,est)
        if t["forca"]<3: return None
        if t["bl_bull"] and t["dir"]=="BUY": return None
        if t["bl_bear"] and t["dir"]=="SELL": return None
        score=s["score_bonus"] if s["operar"] else 0; raz=[]
        if est["romp"]: score+=30; raz.append("Rompimento +30")
        elif est["tendencia"]!="NEUTRO": score+=15; raz.append(f"Tendencia {est['tendencia']} +15")
        if est["choch"]: score+=10; raz.append("CHoCH +10")
        for c in t["conf"]: score+=5; raz.append(f"{c} +5")
        dr=t["dir"]
        if dr=="BUY":
            if smc["ob_bull"]: score+=10; raz.append("OB Bull +10")
            if smc["fvg_bull"]: score+=10; raz.append("FVG Bull +10")
            if smc["cf"]: score+=10; raz.append("Liquidez fundo +10")
        else:
            if smc["ob_bear"]: score+=10; raz.append("OB Bear +10")
            if smc["fvg_bear"]: score+=10; raz.append("FVG Bear +10")
            if smc["ct"]: score+=10; raz.append("Liquidez topo +10")
        p=float(d["Close"].iloc[-1]); a=float(d["atr"].iloc[-1])
        r=calc_risco(p,dr,a,t["rsi"])
        sinal=score>=70 and r["pips"]>0
        return {"par":par.replace("=X","").replace("=F",""),"dir":dr,"score":score,"sinal":sinal,"preco":round(p,5),"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"rsi":t["rsi"],"tend":est["tendencia"],"conf":t["forca"],"sessao":s["sessao"],"operar":s["operar"],"raz":raz,"dados":d,"est":est,"smc":smc}
    except: return None

def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK: return
        resend.api_key = os.environ.get("RESEND_API_KEY","")
        email_destino = os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        corpo = f"SINAL SMC\nPar: {sinal['par']} {sinal['dir']}\nScore: {sinal['score']}%\nPreco: {sinal['preco']}\nSL: {sinal['sl']} TP1: {sinal['tp1']} TP2: {sinal['tp2']}\nR:R: {sinal['rr']} | Risco: {sinal['pips']} pips\n{chr(10).join(sinal['raz'])}"
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":f"SINAL: {sinal['par']} {sinal['dir']} {sinal['score']}%","text":corpo})
    except: pass

sinais_enviados = set()

def monitor_background():
    global sinais_enviados
    while True:
        try:
            for par in pares:
                r=analisar(par)
                if r and r["sinal"]:
                    chave=f"{r['par']}_{r['dir']}"
                    if chave not in sinais_enviados:
                        enviar_email_sinal(r)
                        sinais_enviados.add(chave)
                        if len(sinais_enviados)>50: sinais_enviados.clear()
        except: pass
        time.sleep(300)

def grafico(d, est, smc, par):
    df=d.tail(80)
    fig=go.Figure(go.Candlestick(x=df.index,open=df["Open"],high=df["High"],low=df["Low"],close=df["Close"],name=par,increasing_line_color="#26a69a",decreasing_line_color="#ef5350"))
    fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
    fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
    fig.add_trace(go.Scatter(x=df.index,y=df["ema200"],name="EMA200",line=dict(color="red",width=1)))
    if est["ush"]: fig.add_hline(y=est["ush"],line_dash="dash",line_color="red",annotation_text="Swing High")
    if est["usl"]: fig.add_hline(y=est["usl"],line_dash="dash",line_color="green",annotation_text="Swing Low")
    if smc["ob_bull"]: fig.add_hrect(y0=smc["ob_bull"][0],y1=smc["ob_bull"][1],fillcolor="rgba(38,166,154,0.15)",line_width=0,annotation_text="OB Bull")
    if smc["ob_bear"]: fig.add_hrect(y0=smc["ob_bear"][0],y1=smc["ob_bear"][1],fillcolor="rgba(239,83,80,0.15)",line_width=0,annotation_text="OB Bear")
    fig.update_layout(title=f"{par} M15",xaxis_rangeslider_visible=False,height=450,template="plotly_dark",showlegend=True)
    return fig

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = True
    th = threading.Thread(target=monitor_background, daemon=True)
    th.start()

st.title("SMC Signals Pro")
st.caption("BOS + RSI + EMAs + SMC + Gestao de risco | Monitor 24/7")
s=sessao_activa()
c1,c2,c3,c4=st.columns(4)
c1.metric("Sessao",s["sessao"])
c2.metric("Qualidade",s["qualidade"])
c3.metric("Operar","SIM" if s["operar"] else "NAO")
c4.metric("Hora UTC",datetime.now(timezone.utc).strftime("%H:%M"))
st.divider()

if st.button("Analisar todos os pares", type="primary"):
    resultados=[]
    prog=st.progress(0)
    for i,par in enumerate(pares):
        r=analisar(par, ignorar_sessao=True)
        if r: resultados.append(r)
        prog.progress((i+1)/len(pares))
    sinais=[r for r in resultados if r["sinal"]]
    if sinais:
        st.success(f"{len(sinais)} SINAL(IS) CONFIRMADO(S)!")
        for s in sinais:
            with st.expander(f"SINAL: {s['par']} {s['dir']} | Score: {s['score']}% | RSI: {s['rsi']}", expanded=True):
                c1,c2,c3,c4,c5=st.columns(5)
                c1.metric("Direccao",s["dir"])
                c2.metric("Score",f"{s['score']}%")
                c3.metric("SL",s["sl"])
                c4.metric("TP1",s["tp1"])
                c5.metric("TP2",s["tp2"])
                st.caption(f"R:R {s['rr']} | Risco: {s['pips']} pips")
                st.caption(" | ".join(s["raz"]))
                st.plotly_chart(grafico(s["dados"],s["est"],s["smc"],s["par"]),use_container_width=True)
    else:
        st.info("Sem sinais confirmados. Aguarda confluencia.")
    if resultados:
        st.subheader("Todos os pares")
        linhas=[]
        for r in resultados:
            status="SINAL" if r["sinal"] else "QUASE" if r["score"]>=50 else "AGUARDA"
            linhas.append({"Status":status,"Par":r["par"],"Direccao":r["dir"],"Score":f"{r['score']}%","RSI":r["rsi"],"Tendencia":r["tend"],"SL":r["sl"],"TP1":r["tp1"],"TP2":r["tp2"]})
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption(f"Actualizado: {datetime.now().strftime('%H:%M:%S')} | Email automatico >= 70%")

# v4 2026-05-30 12:30