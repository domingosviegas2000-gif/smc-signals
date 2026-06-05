import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import plotly.graph_objects as go
import threading
import time
import gc
import os
try:
    import resend
    RESEND_OK = True
except:
    RESEND_OK = False
from datetime import datetime, timezone

st.set_page_config(page_title="SMC Scanner Pro", page_icon="📈", layout="wide")

pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","GC=F"]
nomes = {"EURUSD=X":"EURUSD","GBPUSD=X":"GBPUSD","USDJPY=X":"USDJPY","GC=F":"XAUUSD"}

def obter_dados(par, intervalo="15m", periodo="7d"):
    try:
        d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except: return pd.DataFrame()

def adicionar_indicadores(df):
    if len(df)<20: return df
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["ema20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["Close"], window=50).ema_indicator()
    df["ema100"] = ta.trend.EMAIndicator(df["Close"], window=100).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"],df["Low"],df["Close"],window=14).average_true_range()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","score_bonus":20,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","score_bonus":15,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","score_bonus":15,"operar":True}
    else: return {"sessao":"ASIATICA","score_bonus":0,"operar":False}

def classificar(score):
    if score>=85: return "PREMIUM","🟢"
    elif score>=70: return "NORMAL","🟡"
    elif score>=60: return "ACEITAVEL","🔵"
    else: return "FRACO","⚫"

# FASE 1: H1 — sempre BULLISH ou BEARISH
def fase1_h1(par):
    try:
        d=obter_dados(par,"1h","10d")
        if len(d)<10: return "BULLISH"
        d=adicionar_indicadores(d)
        if len(d)<3: return "BULLISH"
        p=float(d["Close"].iloc[-1])
        e20=float(d["ema20"].iloc[-1])
        del d; gc.collect()
        return "BULLISH" if p>e20 else "BEARISH"
    except: return "BULLISH"

# FASE 2: Liquidez M15 — VARRIDA / PROXIMA / NAO OCORREU
def fase2_liquidez(df):
    try:
        df2=df.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
        sh=df2[df2["sh"]]["High"]
        sl=df2[df2["sl"]]["Low"]
        if len(sh)<2 or len(sl)<2: return "NAO OCORREU",False,False,0,0
        p=float(df["Close"].iloc[-1])
        h15=df["High"].tail(15)
        l15=df["Low"].tail(15)
        ush1=float(sh.iloc[-1]); ush2=float(sh.iloc[-2])
        usl1=float(sl.iloc[-1]); usl2=float(sl.iloc[-2])
        nivel_low=min(usl1,usl2)
        nivel_high=max(ush1,ush2)
        liq_bull=(float(l15.min())<=usl1 or float(l15.min())<=usl2) and p>usl1
        liq_bear=(float(h15.max())>=ush1 or float(h15.max())>=ush2) and p<ush1
        prox_bull=abs(p-nivel_low)/nivel_low<0.003 if not liq_bull else False
        prox_bear=abs(p-nivel_high)/nivel_high<0.003 if not liq_bear else False
        if liq_bull or liq_bear: estado="VARRIDA"
        elif prox_bull or prox_bear: estado="PROXIMA"
        else: estado="NAO OCORREU"
        return estado,liq_bull,liq_bear,round(nivel_low,5),round(nivel_high,5)
    except: return "NAO OCORREU",False,False,0,0

# FASE 3: BOS/CHoCH M15 — sempre mostra estado real
def fase3_bos(df):
    try:
        df2=df.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2))&(df2["High"]>df2["High"].shift(3))&(df2["High"]>df2["High"].shift(-3)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2))&(df2["Low"]<df2["Low"].shift(3))&(df2["Low"]<df2["Low"].shift(-3)))
        sh=df2[df2["sh"]]["High"]
        sl=df2[df2["sl"]]["Low"]
        if len(sh)<3 or len(sl)<3: return False,False,"SEM ESTRUTURA","NEUTRO"
        ush=sh.iloc[-1]; psh=sh.iloc[-2]
        usl=sl.iloc[-1]; psl=sl.iloc[-2]
        p=df["Close"].iloc[-1]; pp=df["Close"].iloc[-2]
        bos_bull=(p>ush)and(pp>ush)
        bos_bear=(p<usl)and(pp<usl)
        hh=ush>psh; hl=usl>psl; lh=ush<psh; ll=usl<psl
        t="BULLISH" if(hh and hl)else "BEARISH" if(lh and ll)else "NEUTRO"
        choch_bull=(lh and ll)and bos_bull
        choch_bear=(hh and hl)and bos_bear
        bos_ok=bos_bull or bos_bear
        choch_ok=choch_bull or choch_bear
        if choch_ok: tipo="CHoCH"
        elif bos_ok: tipo="BOS"
        else: tipo="AGUARDA"
        return bos_ok,choch_ok,tipo,t
    except: return False,False,"AGUARDA","NEUTRO"

# FASE 4: Volume
def fase4_volume(df, par):
    try:
        eh_ouro="GC" in par
        u=df.iloc[-1]
        corpo=float(u["corpo"]); media=float(u["media_corpo"])
        vol=float(u["Volume"]); vol_med=float(u["vol_medio"]) if float(u["vol_medio"])>0 else 1
        if eh_ouro:
            if corpo>media*1.5: return "FORTE",True
            elif corpo>media*0.8: return "MEDIO",False
            else: return "FRACO",False
        else:
            if vol>vol_med*1.3 and corpo>media*1.2: return "FORTE",True
            elif vol>vol_med*0.7: return "MEDIO",False
            else: return "FRACO",False
    except: return "MEDIO",False

# FASE 5: Reteste M5 — CONFIRMADO / AGUARDA / NAO OCORREU
def fase5_reteste_m5(par, dr):
    try:
        d=obter_dados(par,"5m","2d")
        if len(d)<10: return "AGUARDA",False,0,0,0
        d=adicionar_indicadores(d)
        if len(d)<5: return "AGUARDA",False,0,0,0
        preco=round(float(d["Close"].iloc[-1]),5)
        high=round(float(d["High"].iloc[-1]),5)
        low=round(float(d["Low"].iloc[-1]),5)
        u=d.iloc[-1]
        corpo=float(u["corpo"]); media=float(u["media_corpo"])
        e20=float(d["ema20"].iloc[-1])
        if dr=="BUY":
            vela_ok=float(u["Close"])>float(u["Open"]) and corpo>media*0.8
            alinhado=preco>e20
        else:
            vela_ok=float(u["Close"])<float(u["Open"]) and corpo>media*0.8
            alinhado=preco<e20
        df2=d.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
        sh=df2[df2["sh"]]["High"]; sl=df2[df2["sl"]]["Low"]
        bos_m5=False
        if len(sh)>=2 and len(sl)>=2:
            ush=float(sh.iloc[-1]); usl=float(sl.iloc[-1])
            p=float(d["Close"].iloc[-1]); pp=float(d["Close"].iloc[-2])
            bos_m5=(p>ush and pp>ush and dr=="BUY") or (p<usl and pp<usl and dr=="SELL")
        confirmado=vela_ok or bos_m5
        estado="CONFIRMADO" if confirmado else "AGUARDA"
        del d,df2; gc.collect()
        return estado,confirmado,preco,high,low
    except:
        gc.collect()
        return "AGUARDA",False,0,0,0

def tipo_ordem(reteste_ok, liq_estado, vol_forte, dr):
    if reteste_ok: return "MARKET ORDER"
    elif liq_estado=="VARRIDA": return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"
    elif vol_forte: return "BUY STOP" if dr=="BUY" else "SELL STOP"
    else: return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"

def calc_risco(preco, dr, high_m5, low_m5, atr, par):
    p=float(preco); a=float(atr)
    eh_ouro="GC" in par
    mult=1.0 if eh_ouro else 0.5
    if dr=="BUY":
        sl=round(low_m5-a*mult,5) if low_m5>0 else round(p-a*1.5,5)
        if sl>=p: sl=round(p-a,5)
        risco=abs(p-sl)
        tp1=round(p+risco*2,5); tp2=round(p+risco*4,5)
    else:
        sl=round(high_m5+a*mult,5) if high_m5>0 else round(p+a*1.5,5)
        if sl<=p: sl=round(p+a,5)
        risco=abs(sl-p)
        tp1=round(p-risco*2,5); tp2=round(p-risco*4,5)
    r=abs(p-sl)
    pips=round(r,2) if eh_ouro else round(r*10000,1)
    unidade="USD" if eh_ouro else "pips"
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":pips,"unidade":unidade,"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def analisar(par, ignorar_sessao=False):
    try:
        nome=nomes.get(par,par.replace("=X","").replace("=F",""))
        s=sessao_activa()
        sessao_ok=s["operar"] if not ignorar_sessao else True
        d15=obter_dados(par,"15m","7d")
        if len(d15)<30:
            gc.collect()
            return None
        d15=adicionar_indicadores(d15)
        if len(d15)<10:
            gc.collect()
            return None
        preco_m15=round(float(d15["Close"].iloc[-1]),5)
        rsi=round(float(d15["rsi"].iloc[-1]),1)
        atr=float(d15["atr"].iloc[-1])
        t_h1=fase1_h1(par)
        liq_estado,liq_bull,liq_bear,nivel_low,nivel_high=fase2_liquidez(d15)
        bos_ok,choch_ok,tipo_bos,tend_m15=fase3_bos(d15)
        volume,vol_forte=fase4_volume(d15,par)
        e20_m15=float(d15["ema20"].iloc[-1])
        p_m15=float(d15["Close"].iloc[-1])
        tend_m15_ema="BULLISH" if p_m15>e20_m15 else "BEARISH"
        if tend_m15=="NEUTRO": tend_m15=tend_m15_ema
        liq_ok=liq_bull or liq_bear
        if liq_bull: dr="BUY"
        elif liq_bear: dr="SELL"
        elif tend_m15=="BULLISH" and t_h1=="BULLISH": dr="BUY"
        elif tend_m15=="BEARISH" and t_h1=="BEARISH": dr="SELL"
        elif t_h1=="BULLISH": dr="BUY"
        else: dr="SELL"
        ret_estado,reteste_ok,preco_m5,high_m5,low_m5=fase5_reteste_m5(par,dr)
        preco_entrada=preco_m5 if reteste_ok and preco_m5>0 else preco_m15
        score=0; raz=[]
        if liq_ok: score+=20; raz.append("Liquidez varrida +20")
        if bos_ok: score+=15; raz.append(tipo_bos+" M15 +15")
        if reteste_ok: score+=15; raz.append("Reteste M5 confirmado +15")
        if t_h1==dr: score+=20; raz.append("H1 alinhado +20")
        elif t_h1!=dr: score+=0; raz.append("H1 contra direcao")
        if volume=="FORTE": score+=15; raz.append("Volume forte +15")
        elif volume=="MEDIO": score+=8; raz.append("Volume medio +8")
        if sessao_ok: score+=s["score_bonus"]; raz.append("Sessao "+s["sessao"])
        if choch_ok: score+=5; raz.append("CHoCH +5")
        classificacao,emoji=classificar(score)
        ordem=tipo_ordem(reteste_ok,liq_estado,vol_forte,dr)
        r=calc_risco(preco_entrada,dr,high_m5,low_m5,atr,par)
        sinal=liq_ok and bos_ok and reteste_ok and score>=60 and r["pips"]>0
        result={"par":nome,"score":score,"classificacao":classificacao,"emoji":emoji,"sinal":sinal,"dir":dr,"preco":preco_entrada,"preco_m15":preco_m15,"preco_m5":preco_m5,"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],"rsi":rsi,"tend_h1":t_h1,"tend_m15":tend_m15,"volume":volume,"atr":round(atr,5),"liq_estado":liq_estado,"liq_ok":liq_ok,"bos_ok":bos_ok,"tipo_bos":tipo_bos,"ret_estado":ret_estado,"reteste_ok":reteste_ok,"ordem":ordem,"nivel_low":nivel_low,"nivel_high":nivel_high,"sessao":s["sessao"],"operar":s["operar"],"raz":raz}
        del d15; gc.collect()
        return result
    except:
        gc.collect()
        return None

def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK: return
        resend.api_key=os.environ.get("RESEND_API_KEY","")
        email_destino=os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        corpo=(
            sinal["emoji"]+" SINAL SMC — "+str(sinal["classificacao"])+chr(10)+
            "================================"+chr(10)+
            "Par:        "+sinal["par"]+chr(10)+
            "Direccao:   "+sinal["dir"]+chr(10)+
            "Ordem:      "+sinal["ordem"]+chr(10)+
            "Entrada:    "+str(sinal["preco"])+chr(10)+
            "Score:      "+str(sinal["score"])+"%"+chr(10)+
            "RSI:        "+str(sinal["rsi"])+chr(10)+
            "Volume:     "+sinal["volume"]+chr(10)+
            "H1:         "+sinal["tend_h1"]+chr(10)+
            "M15:        "+sinal["tend_m15"]+chr(10)+chr(10)+
            "3 FASES OBRIGATORIAS"+chr(10)+
            "================================"+chr(10)+
            "Liquidez:   "+sinal["liq_estado"]+chr(10)+
            "BOS/CHoCH:  "+sinal["tipo_bos"]+chr(10)+
            "Reteste M5: "+sinal["ret_estado"]+chr(10)+chr(10)+
            "GESTAO DE RISCO"+chr(10)+
            "================================"+chr(10)+
            "SL:    "+str(sinal["sl"])+chr(10)+
            "TP1:   "+str(sinal["tp1"])+chr(10)+
            "TP2:   "+str(sinal["tp2"])+chr(10)+
            "R:R:   "+sinal["rr"]+chr(10)+
            "Risco: "+str(sinal["pips"])+" "+sinal["unidade"]+chr(10)+chr(10)+
            chr(10).join(sinal["raz"])
        )
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":sinal["emoji"]+" "+sinal["par"]+" "+sinal["dir"]+" | "+sinal["ordem"]+" @ "+str(sinal["preco"])+" | "+str(sinal["score"])+"% "+str(sinal["classificacao"]),"text":corpo})
    except: pass

sinais_enviados=set()

def monitor_background():
    global sinais_enviados
    while True:
        try:
            for par in pares:
                r=analisar(par)
                if r and r.get("sinal"):
                    chave=f"{r['par']}_{r['dir']}"
                    if chave not in sinais_enviados:
                        enviar_email_sinal(r)
                        sinais_enviados.add(chave)
                        if len(sinais_enviados)>20: sinais_enviados.clear()
                time.sleep(2)
        except: pass
        time.sleep(300)
        gc.collect()

def grafico(par, dr, nivel, preco_entrada):
    try:
        d=obter_dados(par,"15m","2d")
        if len(d)<10: return None
        d=adicionar_indicadores(d)
        df=d.tail(60)
        fig=go.Figure(go.Candlestick(x=df.index,open=df["Open"],high=df["High"],low=df["Low"],close=df["Close"],name=par,increasing_line_color="#26a69a",decreasing_line_color="#ef5350"))
        if "ema20" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
        if "ema50" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
        if "ema100" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema100"],name="EMA100",line=dict(color="red",width=1)))
        if nivel>0: fig.add_hline(y=nivel,line_dash="dot",line_color="yellow",annotation_text="Liquidez")
        if preco_entrada>0: fig.add_hline(y=preco_entrada,line_dash="dash",line_color="white",annotation_text="Entrada")
        fig.update_layout(title=f"{par} M15",xaxis_rangeslider_visible=False,height=420,template="plotly_dark",showlegend=False)
        del d; gc.collect()
        return fig
    except: return None

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started=True
    th=threading.Thread(target=monitor_background,daemon=True)
    th.start()

st.title("SMC Scanner Pro")
st.caption("EURUSD | GBPUSD | USDJPY | XAUUSD | H1+M15+M5 | Monitor 24/7")
s=sessao_activa()
c1,c2,c3,c4=st.columns(4)
c1.metric("Sessao",s["sessao"])
c2.metric("Hora UTC",datetime.now(timezone.utc).strftime("%H:%M"))
c3.metric("Operar","SIM" if s["operar"] else "NAO")
c4.metric("Ativos","EURUSD GBPUSD USDJPY XAUUSD")
st.divider()

if st.button("Analisar mercado",type="primary"):
    resultados=[]
    prog=st.progress(0)
    for i,par in enumerate(pares):
        r=analisar(par,ignorar_sessao=True)
        if r: resultados.append(r)
        prog.progress((i+1)/len(pares))
        gc.collect()

    sinais=[r for r in resultados if r.get("sinal")]

    if sinais:
        st.success(str(len(sinais))+" SINAL(IS) ENCONTRADO(S)!")
        for s in sinais:
            with st.expander(s["emoji"]+" "+s["par"]+" "+s["dir"]+" | "+s["ordem"]+" @ "+str(s["preco"])+" | "+str(s["score"])+"% "+str(s["classificacao"]),expanded=True):
                st.markdown("### "+s["emoji"]+" "+s["par"]+" — "+s["dir"]+" — "+str(s["classificacao"]))
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Entrada",s["preco"])
                c3.metric("Score",str(s["score"])+"%")
                c4.metric("Classe",str(s["classificacao"]))
                c1b,c2b,c3b,c4b=st.columns(4)
                c1b.metric("Stop Loss",s["sl"])
                c2b.metric("TP1",s["tp1"])
                c3b.metric("TP2",s["tp2"])
                c4b.metric("Risco",str(s["pips"])+" "+s["unidade"])
                c1c,c2c,c3c,c4c=st.columns(4)
                c1c.metric("H1",s["tend_h1"])
                c2c.metric("M15",s["tend_m15"])
                c3c.metric("Volume",s["volume"])
                c4c.metric("RSI",s["rsi"])
                st.markdown("**3 Fases Obrigatorias:**")
                col1,col2,col3=st.columns(3)
                col1.metric("Liquidez M15",s["liq_estado"])
                col2.metric("Estrutura M15",s["tipo_bos"])
                col3.metric("Reteste M5",s["ret_estado"])
                st.caption("R:R "+s["rr"]+" | ATR: "+str(s["atr"]))
                st.caption(" | ".join(s["raz"]))
                par_key=[p for p in pares if nomes.get(p,"")==s["par"]]
                nivel=s["nivel_low"] if s["dir"]=="BUY" else s["nivel_high"]
                fig=grafico(par_key[0] if par_key else s["par"],s["dir"],nivel,s["preco"])
                if fig: st.plotly_chart(fig,use_container_width=True)
    else:
        st.info("Sem sinais. Aguarda: Liquidez VARRIDA + BOS/CHoCH + Reteste M5 CONFIRMADO.")

    if resultados:
        st.subheader("Estado do mercado")
        linhas=[]
        for r in resultados:
            obrig=sum([r.get("liq_ok",False),r.get("bos_ok",False),r.get("reteste_ok",False)])
            if r.get("sinal"): status=r.get("emoji","")+" "+str(r.get("classificacao",""))
            elif obrig==2: status="QUASE (2/3)"
            elif obrig==1: status="A FORMAR (1/3)"
            else: status="AGUARDA"
            linhas.append({
                "Status":status,
                "Par":r["par"],
                "Dir":r.get("dir",""),
                "Score":str(r.get("score",0))+"%",
                "RSI":r.get("rsi",0),
                "H1":r.get("tend_h1",""),
                "M15":r.get("tend_m15",""),
                "Volume":r.get("volume",""),
                "Liquidez":r.get("liq_estado",""),
                "BOS/CHoCH":r.get("tipo_bos",""),
                "Reteste M5":r.get("ret_estado",""),
                "Entrada":r.get("preco",0),
                "SL":r.get("sl",0),
                "TP1":r.get("tp1",0),
                "TP2":r.get("tp2",0)
            })
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | PREMIUM>=85% NORMAL>=70% ACEITAVEL>=60%")
