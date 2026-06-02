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

st.set_page_config(page_title="SMC Signals Pro v4", page_icon="📈", layout="wide")
pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","AUDUSD=X","NZDUSD=X","USDCAD=X","GC=F"]

def obter_dados(par, intervalo="15m", periodo="5d"):
    d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True)
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
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","qualidade":"EXCELENTE","score_bonus":15,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","qualidade":"BOA","score_bonus":10,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","qualidade":"BOA","score_bonus":10,"operar":True}
    else: return {"sessao":"ASIATICA","qualidade":"FRACA","score_bonus":0,"operar":False}

def mercado_valido(df):
    atr = float(df["atr"].iloc[-1])
    atr_medio = float(df["atr"].tail(50).mean())
    if atr < atr_medio * 0.7: return False, "ATR baixo"
    e20 = float(df["ema20"].iloc[-1]); e50 = float(df["ema50"].iloc[-1])
    if abs(e20-e50)/e50 < 0.0003: return False, "EMAs proximas"
    velas_pequenas = (df["corpo"].tail(10) < df["media_corpo"].tail(10) * 0.5).sum()
    if velas_pequenas >= 7: return False, "Mercado lateral"
    return True, "OK"

def detectar_estrutura(df):
    df = df.copy()
    df["sh"] = ((df["High"]>df["High"].shift(1))&(df["High"]>df["High"].shift(-1))&(df["High"]>df["High"].shift(2))&(df["High"]>df["High"].shift(-2))&(df["High"]>df["High"].shift(3))&(df["High"]>df["High"].shift(-3)))
    df["sl"] = ((df["Low"]<df["Low"].shift(1))&(df["Low"]<df["Low"].shift(-1))&(df["Low"]<df["Low"].shift(2))&(df["Low"]<df["Low"].shift(-2))&(df["Low"]<df["Low"].shift(3))&(df["Low"]<df["Low"].shift(-3)))
    sh=df[df["sh"]]["High"]; sl=df[df["sl"]]["Low"]
    if len(sh)<3 or len(sl)<3:
        return {"bos_bull":False,"bos_bear":False,"choch":False,"tendencia":"NEUTRO","romp":False,"ush":0,"usl":0,"sh_list":[],"sl_list":[]}
    ush=sh.iloc[-1]; psh=sh.iloc[-2]; usl=sl.iloc[-1]; psl=sl.iloc[-2]
    p=df["Close"].iloc[-1]; pp=df["Close"].iloc[-2]
    bb=(p>ush)and(pp>ush); bs=(p<usl)and(pp<usl)
    hh=ush>psh; hl=usl>psl; lh=ush<psh; ll=usl<psl
    t="BULLISH" if(hh and hl)else "BEARISH" if(lh and ll)else "NEUTRO"
    ch=((hh and hl)and bs)or((lh and ll)and bb)
    return {"bos_bull":bb,"bos_bear":bs,"choch":ch,"tendencia":t,"romp":bb or bs,"ush":round(float(ush),5),"usl":round(float(usl),5),"sh_list":list(sh.tail(5).values),"sl_list":list(sl.tail(5).values)}

def detectar_sweep(df, est):
    if not est["sh_list"] or not est["sl_list"]: return {"sweep_bull":False,"sweep_bear":False}
    p=float(df["Close"].iloc[-1])
    h5=df["High"].tail(5); l5=df["Low"].tail(5)
    ush=est["sh_list"][-1]; usl=est["sl_list"][-1]
    return {"sweep_bull":(float(l5.min())<usl)and(p>usl),"sweep_bear":(float(h5.max())>ush)and(p<ush)}

def detectar_smc(df):
    df=df.copy(); df["vf"]=df["corpo"]>df["media_corpo"]*1.5
    ob_bull=None; ob_bear=None
    for i in range(len(df)-3,len(df)-25,-1):
        if df["vf"].iloc[i]:
            if df["Close"].iloc[i]<df["Open"].iloc[i]:
                if df["Close"].iloc[-1]>df["High"].iloc[i]: ob_bull=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
            else:
                if df["Close"].iloc[-1]<df["Low"].iloc[i]: ob_bear=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
    fb=False; fs=False; fbr=None; fsr=None
    for i in range(len(df)-2,len(df)-20,-1):
        if i<1 or i+1>=len(df): continue
        if df["Low"].iloc[i+1]>df["High"].iloc[i-1]: fb=True; fbr=(round(float(df["High"].iloc[i-1]),5),round(float(df["Low"].iloc[i+1]),5)); break
        if df["High"].iloc[i+1]<df["Low"].iloc[i-1]: fs=True; fsr=(round(float(df["High"].iloc[i+1]),5),round(float(df["Low"].iloc[i-1]),5)); break
    h=df["High"].tail(30); l=df["Low"].tail(30); p=df["Close"].iloc[-1]
    lt=round(float(h.max()),5); lf=round(float(l.min()),5)
    return {"ob_bull":ob_bull,"ob_bear":ob_bear,"fvg_bull":fb,"fvg_bear":fs,"fbr":fbr,"fsr":fsr,"lt":lt,"lf":lf,"ct":abs(p-lt)/lt<0.0008,"cf":abs(p-lf)/lf<0.0008}

def detectar_reteste(df, smc, dr):
    p=float(df["Close"].iloc[-1])
    if dr=="BUY":
        if smc["ob_bull"] and smc["ob_bull"][0]<=p<=smc["ob_bull"][1]*1.001: return True,"Reteste OB Bull"
        if smc["fbr"] and smc["fbr"][0]<=p<=smc["fbr"][1]*1.001: return True,"Reteste FVG Bull"
    else:
        if smc["ob_bear"] and smc["ob_bear"][0]*0.999<=p<=smc["ob_bear"][1]: return True,"Reteste OB Bear"
        if smc["fsr"] and smc["fsr"][0]*0.999<=p<=smc["fsr"][1]: return True,"Reteste FVG Bear"
    return False,""

def forca_movimento(df):
    u=df.iloc[-1]
    return float(u["corpo"])>float(u["media_corpo"])*1.3

def tendencia_h1(par):
    try:
        d=obter_dados(par,intervalo="1h",periodo="10d")
        d=adicionar_indicadores(d)
        e20=float(d["ema20"].iloc[-1]); e50=float(d["ema50"].iloc[-1]); p=float(d["Close"].iloc[-1])
        if p>e20>e50: return "BULLISH"
        elif p<e20<e50: return "BEARISH"
        else: return "NEUTRO"
    except: return "NEUTRO"

def confirmar(df, est):
    p=float(df["Close"].iloc[-1]); rsi=float(df["rsi"].iloc[-1])
    e20=float(df["ema20"].iloc[-1]); e50=float(df["ema50"].iloc[-1]); e200=float(df["ema200"].iloc[-1])
    cb=[]; cs=[]
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
    dr="BUY" if len(cb)>=len(cs) else "SELL"
    c=cb if dr=="BUY" else cs
    return {"dir":dr,"forca":len(c),"conf":c,"rsi":round(rsi,1),"ema200_alinhada": (p>e200 and dr=="BUY") or (p<e200 and dr=="SELL")}

def rsi_valido(rsi, dr):
    if dr=="BUY": return 45<=rsi<=68
    else: return 32<=rsi<=55

def tipo_ordem(sweep, reteste, dr):
    if reteste: return "LIMIT ORDER" if dr=="BUY" else "SELL LIMIT"
    elif sweep: return "MARKET ORDER"
    else: return "BUY STOP" if dr=="BUY" else "SELL STOP"

def checklist_qualidade(sweep, reteste, ema200_ok, h1, atr_ok):
    items = [
        ("Liquidity Sweep", sweep),
        ("Reteste do rompimento", reteste),
        ("EMA200 alinhada", ema200_ok),
        ("H1 confirma tendencia", h1),
        ("ATR acima da media", atr_ok)
    ]
    aprovados = sum(1 for _, v in items if v)
    return items, aprovados

def calc_risco(preco, dr, atr, rsi, par):
    p=float(preco); a=float(atr)
    eh_ouro = "GC" in par or "XAU" in par
    m=1.5 if(rsi>60 or rsi<40)else 2.0
    if dr=="BUY":
        sl=round(p-a*m,5); tp1=round(p+a*2,5); tp2=round(p+a*4,5)
        if sl>=p: sl=round(p-a,5)
        if tp1<=p: tp1=round(p+a,5)
    else:
        sl=round(p+a*m,5); tp1=round(p-a*2,5); tp2=round(p-a*4,5)
        if sl<=p: sl=round(p+a,5)
        if tp1>=p: tp1=round(p-a,5)
    r=abs(p-sl)
    pips = round(r,2) if eh_ouro else round(r*10000,1)
    unidade = "USD" if eh_ouro else "pips"
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":pips,"unidade":unidade,"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def analisar(par, ignorar_sessao=False):
    try:
        d=obter_dados(par)
        if len(d)<100: return None
        d=adicionar_indicadores(d)
        s=sessao_activa()
        if not ignorar_sessao and not s["operar"]: return None
        valido,motivo=mercado_valido(d)
        preco=round(float(d["Close"].iloc[-1]),5)
        rsi_val=round(float(d["rsi"].iloc[-1]),1)
        nome=par.replace("=X","").replace("=F","")
        atr_ok = float(d["atr"].iloc[-1]) >= float(d["atr"].tail(50).mean()) * 0.7
        if not valido: return {"par":nome,"bloqueado":True,"motivo":motivo,"score":0,"sinal":False,"dir":"","preco":preco,"rsi":rsi_val,"tend":"NEUTRO","tend_h1":"","sweep":False,"reteste":False,"forca":False,"ema200_ok":False,"atr_ok":atr_ok,"checklist":[],"aprovados":0,"ordem":"","sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","raz":[motivo],"sessao":s["sessao"],"operar":s["operar"]}
        est=detectar_estrutura(d)
        smc=detectar_smc(d)
        t=confirmar(d,est)
        dr=t["dir"]
        if not rsi_valido(t["rsi"],dr): return {"par":nome,"bloqueado":True,"motivo":f"RSI {t['rsi']} fora zona","score":0,"sinal":False,"dir":dr,"preco":preco,"rsi":t["rsi"],"tend":est["tendencia"],"tend_h1":"","sweep":False,"reteste":False,"forca":False,"ema200_ok":False,"atr_ok":atr_ok,"checklist":[],"aprovados":0,"ordem":"","sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","raz":["RSI bloqueado"],"sessao":s["sessao"],"operar":s["operar"]}
        if not est["romp"] and not est["choch"]: return {"par":nome,"bloqueado":True,"motivo":"Sem BOS","score":0,"sinal":False,"dir":dr,"preco":preco,"rsi":t["rsi"],"tend":est["tendencia"],"tend_h1":"","sweep":False,"reteste":False,"forca":False,"ema200_ok":False,"atr_ok":atr_ok,"checklist":[],"aprovados":0,"ordem":"","sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","raz":["Sem BOS"],"sessao":s["sessao"],"operar":s["operar"]}
        t_h1=tendencia_h1(par)
        sweep=detectar_sweep(d,est)
        reteste,mot_ret=detectar_reteste(d,smc,dr)
        forca=forca_movimento(d)
        ema200_ok=t["ema200_alinhada"]
        h1_ok = t_h1==est["tendencia"] and t_h1!="NEUTRO"
        checklist,aprovados=checklist_qualidade(sweep["sweep_bull"] or sweep["sweep_bear"],reteste,ema200_ok,h1_ok,atr_ok)
        ordem=tipo_ordem(sweep["sweep_bull"] or sweep["sweep_bear"],reteste,dr)
        score=0; raz=[]
        if s["operar"]: score+=s["score_bonus"]; raz.append(f"Sessao {s['sessao']}")
        if est["romp"]: score+=30; raz.append("BOS confirmado +30")
        if est["choch"]: score+=10; raz.append("CHoCH +10")
        if dr=="BUY" and sweep["sweep_bull"]: score+=25; raz.append("Sweep Bull +25")
        elif dr=="SELL" and sweep["sweep_bear"]: score+=25; raz.append("Sweep Bear +25")
        if reteste: score+=20; raz.append(f"{mot_ret} +20")
        else: score-=20
        if forca: score+=15; raz.append("Forca +15")
        if h1_ok: score+=10; raz.append(f"H1 {t_h1} +10")
        for c in t["conf"][:2]: score+=5; raz.append(f"{c} +5")
        a=float(d["atr"].iloc[-1])
        r=calc_risco(preco,dr,a,t["rsi"],par)
        operar_agora = s["operar"] if not ignorar_sessao else True
        sinal=score>=75 and r["pips"]>0 and operar_agora and aprovados>=3
        return {"par":nome,"bloqueado":False,"dir":dr,"score":score,"sinal":sinal,"preco":preco,"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],"rsi":t["rsi"],"tend":est["tendencia"],"tend_h1":t_h1,"sweep":sweep["sweep_bull"] or sweep["sweep_bear"],"reteste":reteste,"forca":forca,"ema200_ok":ema200_ok,"atr_ok":atr_ok,"checklist":checklist,"aprovados":aprovados,"ordem":ordem,"sessao":s["sessao"],"operar":s["operar"],"raz":raz,"dados":d,"est":est,"smc":smc}
    except: return None

def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK: return
        resend.api_key=os.environ.get("RESEND_API_KEY","")
        email_destino=os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        checklist_txt = "
".join([f"{'OK' if v else 'NOK'} {nome}" for nome,v in sinal["checklist"]])
        corpo=f"""SINAL SMC PRO v4
========================
Par:        {sinal["par"]}
Direccao:   {sinal["dir"]}
Tipo Ordem: {sinal["ordem"]}
Entrada:    {sinal["preco"]}
Score:      {sinal["score"]}%
RSI:        {sinal["rsi"]}
M15:        {sinal["tend"]} | H1: {sinal["tend_h1"]}

CHECKLIST ({sinal["aprovados"]}/5)
========================
{checklist_txt}

GESTAO DE RISCO
========================
SL:    {sinal["sl"]}
TP1:   {sinal["tp1"]}
TP2:   {sinal["tp2"]}
R:R:   {sinal["rr"]}
Risco: {sinal["pips"]} {sinal["unidade"]}

CONFIRMACOES
========================
{chr(10).join(sinal["raz"])}"""
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":f"SINAL: {sinal['par']} {sinal['dir']} | {sinal['ordem']} | Entrada: {sinal['preco']} | Checklist: {sinal['aprovados']}/5","text":corpo})
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
        except: pass
        time.sleep(300)

def grafico(d,est,smc,par):
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
    st.session_state.monitor_started=True
    th=threading.Thread(target=monitor_background,daemon=True)
    th.start()

st.title("SMC Signals Pro v4")
st.caption("Sweep + Reteste + BOS + Checklist 5 pontos | Monitor 24/7 | Sessoes: Londres + NY")
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
    sinais=[r for r in resultados if r.get("sinal")]
    if sinais:
        st.success(f"{len(sinais)} SINAL(IS) CONFIRMADO(S)!")
        for s in sinais:
            with st.expander(f"SINAL: {s['par']} {s['dir']} | {s['ordem']} | Entrada: {s['preco']} | Score: {s['score']}% | Checklist: {s['aprovados']}/5",expanded=True):
                st.markdown(f"### {s['par']} — {s['dir']}")
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Entrada",s["preco"])
                c3.metric("Score",f"{s['score']}%")
                c4.metric("Checklist",f"{s['aprovados']}/5")
                c1b,c2b,c3b,c4b=st.columns(4)
                c1b.metric("Stop Loss",s["sl"])
                c2b.metric("TP1",s["tp1"])
                c3b.metric("TP2",s["tp2"])
                c4b.metric("Risco",f"{s['pips']} {s['unidade']}")
                st.markdown("**Checklist de qualidade:**")
                cols=st.columns(5)
                for idx,(nome,val) in enumerate(s["checklist"]):
                    cols[idx].metric(nome,"OK" if val else "NOK",delta=None)
                st.caption(f"M15: {s['tend']} | H1: {s['tend_h1']} | R:R {s['rr']}")
                st.caption(" | ".join(s["raz"]))
                st.plotly_chart(grafico(s["dados"],s["est"],s["smc"],s["par"]),use_container_width=True)
    else:
        st.info("Sem sinais confirmados. Sistema aguarda sweep + reteste + BOS + checklist >= 3/5.")
    if resultados:
        st.subheader("Todos os pares")
        linhas=[]
        for r in resultados:
            if r.get("bloqueado"): status="BLOQUEADO"
            elif r.get("sinal"): status="SINAL"
            elif r.get("score",0)>=50: status="QUASE"
            else: status="AGUARDA"
            linhas.append({"Status":status,"Par":r["par"],"Dir":r["dir"],"Ordem":r.get("ordem",""),"Entrada":r["preco"],"Score":f"{r['score']}%","RSI":r["rsi"],"Checklist":f"{r.get('aprovados',0)}/5","M15":r["tend"],"H1":r.get("tend_h1",""),"SL":r["sl"],"TP1":r["tp1"],"TP2":r["tp2"]})
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption(f"Actualizado: {datetime.now().strftime(\'%H:%M:%S\')} | Score >= 75% + Checklist >= 3/5 + Sessao activa")
