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

st.set_page_config(page_title="SMC Signals Pro v8", page_icon="📈", layout="wide")
pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","AUDUSD=X","NZDUSD=X","USDCAD=X","GC=F"]

def obter_dados(par, intervalo="15m", periodo="5d"):
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
    df["ema200"] = ta.trend.EMAIndicator(df["Close"], window=200).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"],df["Low"],df["Close"],window=14).average_true_range()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["amplitude"] = df["High"]-df["Low"]
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","qualidade":"EXCELENTE","score_bonus":20,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","qualidade":"BOA","score_bonus":15,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","qualidade":"BOA","score_bonus":15,"operar":True}
    else: return {"sessao":"ASIATICA","qualidade":"FRACA","score_bonus":0,"operar":False}

def classificar_score(score):
    if score>=95: return "PREMIUM","🔥"
    elif score>=85: return "SINAL","✅"
    elif score>=75: return "QUASE","⚡"
    elif score>=60: return "AGUARDA","⏳"
    else: return "RANGE","❌"

def tendencia_tf(par, intervalo, periodo):
    try:
        d=obter_dados(par,intervalo=intervalo,periodo=periodo)
        if len(d)<20: return "NEUTRO"
        d=adicionar_indicadores(d)
        if len(d)<5: return "NEUTRO"
        e20=float(d["ema20"].iloc[-1]); e50=float(d["ema50"].iloc[-1]); p=float(d["Close"].iloc[-1])
        del d; gc.collect()
        if p>e20>e50: return "BULLISH"
        elif p<e20<e50: return "BEARISH"
        else: return "NEUTRO"
    except: return "NEUTRO"

def detectar_bos_m5(par, dr):
    try:
        d=obter_dados(par,intervalo="5m",periodo="1d")
        if len(d)<20: return False, 0, 0, 0
        d=adicionar_indicadores(d)
        if len(d)<10: return False, 0, 0, 0
        d2=d.copy()
        d2["sh"]=((d2["High"]>d2["High"].shift(1))&(d2["High"]>d2["High"].shift(-1))&(d2["High"]>d2["High"].shift(2))&(d2["High"]>d2["High"].shift(-2)))
        d2["sl"]=((d2["Low"]<d2["Low"].shift(1))&(d2["Low"]<d2["Low"].shift(-1))&(d2["Low"]<d2["Low"].shift(2))&(d2["Low"]<d2["Low"].shift(-2)))
        sh=d2[d2["sh"]]["High"]; sl=d2[d2["sl"]]["Low"]
        preco=round(float(d["Close"].iloc[-1]),5)
        high_vela=round(float(d["High"].iloc[-1]),5)
        low_vela=round(float(d["Low"].iloc[-1]),5)
        if len(sh)<2 or len(sl)<2:
            del d,d2; gc.collect()
            return False, preco, high_vela, low_vela
        ush=float(sh.iloc[-1]); usl=float(sl.iloc[-1])
        p=float(d["Close"].iloc[-1]); pp=float(d["Close"].iloc[-2])
        bos_bull=(p>ush)and(pp>ush)
        bos_bear=(p<usl)and(pp<usl)
        bos_ok=(bos_bull and dr=="BUY") or (bos_bear and dr=="SELL")
        del d,d2; gc.collect()
        return bos_ok, preco, high_vela, low_vela
    except:
        gc.collect()
        return False, 0, 0, 0

def detectar_sweep(df):
    df2=df.copy()
    df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
    df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
    sh=df2[df2["sh"]]["High"]; sl=df2[df2["sl"]]["Low"]
    if len(sh)<2 or len(sl)<2: return False,False
    p=float(df["Close"].iloc[-1])
    h3=df["High"].tail(3); l3=df["Low"].tail(3)
    ush=float(sh.iloc[-1]); usl=float(sl.iloc[-1])
    return (float(l3.min())<usl)and(p>usl),(float(h3.max())>ush)and(p<ush)

def detectar_smc(df, dr):
    if len(df)<25: return {"ob":None,"fvg":False,"fbr":None}
    df=df.copy(); df["vf"]=df["corpo"]>df["media_corpo"]*1.5
    ob=None
    for i in range(len(df)-3,max(len(df)-25,0),-1):
        if df["vf"].iloc[i]:
            if dr=="BUY" and df["Close"].iloc[i]<df["Open"].iloc[i]:
                if df["Close"].iloc[-1]>df["High"].iloc[i]: ob=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
            elif dr=="SELL" and df["Close"].iloc[i]>df["Open"].iloc[i]:
                if df["Close"].iloc[-1]<df["Low"].iloc[i]: ob=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
    fvg=False; fbr=None
    for i in range(len(df)-2,max(len(df)-20,1),-1):
        if i+1>=len(df): continue
        if dr=="BUY" and df["Low"].iloc[i+1]>df["High"].iloc[i-1]:
            fvg=True; fbr=(round(float(df["High"].iloc[i-1]),5),round(float(df["Low"].iloc[i+1]),5)); break
        elif dr=="SELL" and df["High"].iloc[i+1]<df["Low"].iloc[i-1]:
            fvg=True; fbr=(round(float(df["High"].iloc[i+1]),5),round(float(df["Low"].iloc[i-1]),5)); break
    return {"ob":ob,"fvg":fvg,"fbr":fbr}

def detectar_reteste(df, smc, dr):
    p=float(df["Close"].iloc[-1])
    if smc["ob"]:
        if dr=="BUY" and smc["ob"][0]<=p<=smc["ob"][1]*1.002: return True,"Reteste OB"
        if dr=="SELL" and smc["ob"][0]*0.998<=p<=smc["ob"][1]: return True,"Reteste OB"
    if smc["fbr"]:
        lo,hi=smc["fbr"]
        if dr=="BUY" and lo<=p<=hi*1.002: return True,"Reteste FVG"
        if dr=="SELL" and lo*0.998<=p<=hi: return True,"Reteste FVG"
    return False,""

def calc_risco(preco, dr, high_m5, low_m5, atr, par):
    p=float(preco); a=float(atr)
    eh_ouro="GC" in par or "XAU" in par
    if dr=="BUY":
        sl=round(low_m5-a*0.5,5) if low_m5>0 else round(p-a*1.5,5)
        if sl>=p: sl=round(p-a,5)
        risco=abs(p-sl)
        tp1=round(p+risco*2,5); tp2=round(p+risco*4,5)
    else:
        sl=round(high_m5+a*0.5,5) if high_m5>0 else round(p+a*1.5,5)
        if sl<=p: sl=round(p+a,5)
        risco=abs(sl-p)
        tp1=round(p-risco*2,5); tp2=round(p-risco*4,5)
    r=abs(p-sl)
    pips=round(r,2) if eh_ouro else round(r*10000,1)
    unidade="USD" if eh_ouro else "pips"
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":pips,"unidade":unidade,"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def tipo_ordem(sweep, reteste, dr):
    if reteste: return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"
    elif sweep: return "MARKET ORDER"
    else: return "BUY STOP" if dr=="BUY" else "SELL STOP"

def analisar(par, ignorar_sessao=False):
    try:
        d15=obter_dados(par,"15m","3d")
        if len(d15)<50: return None
        d15=adicionar_indicadores(d15)
        if len(d15)<10: return None

        s=sessao_activa()
        sessao_ok=s["operar"] if not ignorar_sessao else True
        nome=par.replace("=X","").replace("=F","")
        preco_m15=round(float(d15["Close"].iloc[-1]),5)
        rsi=round(float(d15["rsi"].iloc[-1]),1)
        atr=float(d15["atr"].iloc[-1])
        atr_med=float(d15["atr"].tail(50).mean()) if len(d15)>=50 else atr
        atr_ok=atr>=atr_med*0.7

        # TENDENCIAS
        t_h1=tendencia_tf(par,"1h","5d")
        t_m15=tendencia_tf(par,"15m","3d")

        # ALINHAMENTO H1 + M15
        if t_h1=="NEUTRO" or t_m15=="NEUTRO":
            del d15; gc.collect()
            return {"par":nome,"score":0,"classificacao":"RANGE","emoji":"❌","sinal":False,"dir":"","preco":preco_m15,"preco_m5":0,"rsi":rsi,"tend_h1":t_h1,"tend_m15":t_m15,"atr":round(atr,5),"atr_ok":atr_ok,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","ordem":"","criterios":{},"raz":[],"sessao":s["sessao"],"operar":s["operar"]}

        if t_h1!=t_m15:
            del d15; gc.collect()
            return {"par":nome,"score":20,"classificacao":"AGUARDA","emoji":"⏳","sinal":False,"dir":t_h1,"preco":preco_m15,"preco_m5":0,"rsi":rsi,"tend_h1":t_h1,"tend_m15":t_m15,"atr":round(atr,5),"atr_ok":atr_ok,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","ordem":"","criterios":{"H1 alinhado":True,"M15 alinhado com H1":False},"raz":["H1 e M15 desalinhados"],"sessao":s["sessao"],"operar":s["operar"]}

        dr=t_h1

        # RSI
        rsi_ok=(45<=rsi<=70) if dr=="BUY" else (30<=rsi<=55)
        if not rsi_ok:
            del d15; gc.collect()
            return {"par":nome,"score":25,"classificacao":"AGUARDA","emoji":"⏳","sinal":False,"dir":dr,"preco":preco_m15,"preco_m5":0,"rsi":rsi,"tend_h1":t_h1,"tend_m15":t_m15,"atr":round(atr,5),"atr_ok":atr_ok,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","ordem":"","criterios":{"H1 alinhado":True,"M15 alinhado com H1":True,"RSI favoravel":False},"raz":[f"RSI {rsi} fora zona"],"sessao":s["sessao"],"operar":s["operar"]}

        # BOS M5
        bos_m5,preco_m5,high_m5,low_m5=detectar_bos_m5(par,dr)
        preco_entrada=preco_m5 if bos_m5 and preco_m5>0 else preco_m15

        # QUALIDADE EXTRA
        e200=float(d15["ema200"].iloc[-1])
        p_val=float(d15["Close"].iloc[-1])
        ema200_ok=(p_val>e200 and dr=="BUY") or (p_val<e200 and dr=="SELL")
        sw_bull,sw_bear=detectar_sweep(d15)
        sw_ok=sw_bull if dr=="BUY" else sw_bear
        smc=detectar_smc(d15,dr)
        reteste,mot_ret=detectar_reteste(d15,smc,dr)

        # PONTUACAO
        score=0; raz=[]

        # CRITERIOS OBRIGATORIOS (60 pts)
        score+=15; raz.append("H1 alinhado +15")
        score+=15; raz.append("M15 alinhado com H1 +15")
        if bos_m5: score+=20; raz.append("BOS M5 confirmado +20")
        if rsi_ok: score+=5; raz.append(f"RSI {rsi} +5")
        if sessao_ok: score+=s["score_bonus"]; raz.append(f"Sessao {s['sessao']}")

        # QUALIDADE EXTRA (40 pts)
        if atr_ok: score+=10; raz.append("ATR acima media +10")
        if reteste: score+=10; raz.append(f"{mot_ret} +10")
        if sw_ok: score+=10; raz.append("Liquidity Sweep +10")
        if ema200_ok: score+=10; raz.append("EMA200 alinhada +10")

        criterios={
            "H1 alinhado": True,
            "M15 alinhado com H1": True,
            "BOS M5 confirmado": bos_m5,
            "RSI favoravel": rsi_ok,
            "Sessao activa": sessao_ok,
            "ATR acima media": atr_ok,
            "Reteste OB/FVG": reteste,
            "Liquidity Sweep": sw_ok,
            "EMA200 alinhada": ema200_ok
        }

        classificacao,emoji=classificar_score(score)
        sinal=score>=85 and bos_m5 and sessao_ok
        ordem=tipo_ordem(sw_ok,reteste,dr)
        r=calc_risco(preco_entrada,dr,high_m5,low_m5,atr,par)

        result={"par":nome,"score":score,"classificacao":classificacao,"emoji":emoji,"sinal":sinal,"dir":dr,"preco":preco_entrada,"preco_m15":preco_m15,"preco_m5":preco_m5,"rsi":rsi,"tend_h1":t_h1,"tend_m15":t_m15,"atr":round(atr,5),"atr_ok":atr_ok,"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],"ordem":ordem,"criterios":criterios,"raz":raz,"sessao":s["sessao"],"operar":s["operar"]}
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
        crit_txt=chr(10).join([("OK  " if v else "NOK ") + k for k,v in sinal["criterios"].items()])
        corpo=(
            sinal["emoji"]+" SINAL SMC PRO v8 — "+str(sinal["classificacao"])+chr(10)+
            "================================"+chr(10)+
            "Par:           "+sinal["par"]+chr(10)+
            "Direccao:      "+sinal["dir"]+chr(10)+
            "Tipo Ordem:    "+sinal["ordem"]+chr(10)+
            "Preco Entrada: "+str(sinal["preco"])+" (M5)"+chr(10)+
            "Score:         "+str(sinal["score"])+"%"+chr(10)+
            "RSI:           "+str(sinal["rsi"])+chr(10)+
            "ATR:           "+str(sinal["atr"])+chr(10)+
            "H1: "+sinal["tend_h1"]+" | M15: "+sinal["tend_m15"]+chr(10)+chr(10)+
            "CRITERIOS ("+str(sum(1 for v in sinal["criterios"].values() if v))+"/9)"+chr(10)+
            "================================"+chr(10)+
            crit_txt+chr(10)+chr(10)+
            "GESTAO DE RISCO"+chr(10)+
            "================================"+chr(10)+
            "SL:    "+str(sinal["sl"])+chr(10)+
            "TP1:   "+str(sinal["tp1"])+chr(10)+
            "TP2:   "+str(sinal["tp2"])+chr(10)+
            "R:R:   "+sinal["rr"]+chr(10)+
            "Risco: "+str(sinal["pips"])+" "+sinal["unidade"]+chr(10)+chr(10)+
            chr(10).join(sinal["raz"])
        )
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":sinal["emoji"]+" v8: "+sinal["par"]+" "+sinal["dir"]+" | "+sinal["ordem"]+" @ "+str(sinal["preco"])+" | "+str(sinal["score"])+"%","text":corpo})
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
                        if len(sinais_enviados)>50: sinais_enviados.clear()
                time.sleep(2)
        except: pass
        time.sleep(300)
        gc.collect()

def grafico(par, est_data=None):
    try:
        d=obter_dados(par,"15m","2d")
        if len(d)<10: return None
        d=adicionar_indicadores(d)
        df=d.tail(60)
        fig=go.Figure(go.Candlestick(x=df.index,open=df["Open"],high=df["High"],low=df["Low"],close=df["Close"],name=par,increasing_line_color="#26a69a",decreasing_line_color="#ef5350"))
        if "ema20" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
        if "ema50" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
        if "ema200" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema200"],name="EMA200",line=dict(color="red",width=1)))
        fig.update_layout(title=f"{par} M15",xaxis_rangeslider_visible=False,height=400,template="plotly_dark",showlegend=False)
        del d; gc.collect()
        return fig
    except: return None

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started=True
    th=threading.Thread(target=monitor_background,daemon=True)
    th.start()

st.title("SMC Signals Pro v8")
st.caption("H1 tendencia | M15 confirmacao | M5 entrada | Qualidade acima de quantidade")
s=sessao_activa()
c1,c2,c3,c4=st.columns(4)
c1.metric("Sessao",s["sessao"])
c2.metric("Qualidade",s["qualidade"])
c3.metric("Operar","SIM" if s["operar"] else "NAO")
c4.metric("Hora UTC",datetime.now(timezone.utc).strftime("%H:%M"))
st.divider()

if st.button("Analisar todos os pares",type="primary"):
    resultados=[]
    prog=st.progress(0)
    for i,par in enumerate(pares):
        r=analisar(par,ignorar_sessao=True)
        if r: resultados.append(r)
        prog.progress((i+1)/len(pares))
        gc.collect()

    sinais=[r for r in resultados if r.get("sinal")]

    if sinais:
        st.success(str(len(sinais))+" SINAL(IS) DE ALTA QUALIDADE!")
        for s in sinais:
            with st.expander(s["emoji"]+" "+s["par"]+" "+s["dir"]+" | "+s["ordem"]+" @ "+str(s["preco"])+" | "+str(s["score"])+"%  "+str(s["classificacao"]),expanded=True):
                st.markdown("### "+s["emoji"]+" "+s["par"]+" — "+s["dir"]+" — "+str(s["classificacao"]))
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Entrada M5",s["preco"])
                c3.metric("Score",str(s["score"])+"%")
                c4.metric("RSI",s["rsi"])
                c1b,c2b,c3b,c4b=st.columns(4)
                c1b.metric("Stop Loss",s["sl"])
                c2b.metric("TP1",s["tp1"])
                c3b.metric("TP2",s["tp2"])
                c4b.metric("Risco",str(s["pips"])+" "+s["unidade"])
                c1c,c2c,c3c=st.columns(3)
                c1c.metric("H1",s["tend_h1"])
                c2c.metric("M15",s["tend_m15"])
                c3c.metric("ATR","OK" if s["atr_ok"] else "BAIXO")
                st.markdown("**Criterios:**")
                cols=st.columns(3)
                for idx,(k,v) in enumerate(s["criterios"].items()):
                    cols[idx%3].write(("✅ " if v else "❌ ")+k)
                st.caption("R:R "+s["rr"]+" | M15 ref: "+str(s.get("preco_m15","")))
                fig=grafico(s["par"])
                if fig: st.plotly_chart(fig,use_container_width=True)
    else:
        st.info("Sistema aguarda confluencia H1+M15+M5 com score >= 85%.")

    if resultados:
        st.subheader("Todos os pares")
        linhas=[]
        for r in resultados:
            linhas.append({
                "Status": r.get("emoji","")+" "+r.get("classificacao",""),
                "Par": r["par"],
                "Dir": r.get("dir",""),
                "Score": str(r.get("score",0))+"%",
                "RSI": r.get("rsi",0),
                "H1": r.get("tend_h1",""),
                "M15": r.get("tend_m15",""),
                "ATR": "OK" if r.get("atr_ok") else "BAIXO",
                "Crit": str(sum(1 for v in r.get("criterios",{}).values() if v))+"/9",
                "Entrada": r.get("preco",0),
                "SL": r.get("sl",0),
                "TP1": r.get("tp1",0),
                "TP2": r.get("tp2",0)
            })
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | Sinal>=85% | PREMIUM>=95% | H1+M15+M5")
