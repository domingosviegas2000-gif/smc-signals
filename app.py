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

st.set_page_config(page_title="SMC Signals Pro v7", page_icon="📈", layout="wide")
pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","AUDUSD=X","NZDUSD=X","USDCAD=X","GC=F"]

def obter_dados(par, intervalo="15m", periodo="3d"):
    try:
        d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except: return pd.DataFrame()

def adicionar_indicadores(df):
    if len(df)<50: return df
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["ema20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["Close"], window=50).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["Close"], window=200).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"],df["Low"],df["Close"],window=14).average_true_range()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","qualidade":"EXCELENTE","score_bonus":20,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","qualidade":"BOA","score_bonus":15,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","qualidade":"BOA","score_bonus":15,"operar":True}
    else: return {"sessao":"ASIATICA","qualidade":"FRACA","score_bonus":0,"operar":False}

def detectar_range(df):
    if len(df)<20: return True,["Dados insuficientes"]
    e20=float(df["ema20"].iloc[-1]); e50=float(df["ema50"].iloc[-1])
    atr=float(df["atr"].iloc[-1]); atr_med=float(df["atr"].tail(50).mean()) if len(df)>=50 else atr
    emas_proximas=abs(e20-e50)/e50<0.0003
    atr_baixo=atr<atr_med*0.7
    velas_pequenas=(df["corpo"].tail(10)<df["media_corpo"].tail(10)*0.5).sum()>=7
    motivos=[]
    if emas_proximas: motivos.append("EMAs proximas")
    if atr_baixo: motivos.append("ATR baixo")
    if velas_pequenas: motivos.append("Velas pequenas")
    return bool(motivos),motivos

def detectar_estrutura(df):
    if len(df)<20: return {"romp":False,"choch":False,"tendencia":"NEUTRO","ush":0,"usl":0,"sh_list":[],"sl_list":[]}
    df=df.copy()
    df["sh"]=((df["High"]>df["High"].shift(1))&(df["High"]>df["High"].shift(-1))&(df["High"]>df["High"].shift(2))&(df["High"]>df["High"].shift(-2)))
    df["sl"]=((df["Low"]<df["Low"].shift(1))&(df["Low"]<df["Low"].shift(-1))&(df["Low"]<df["Low"].shift(2))&(df["Low"]<df["Low"].shift(-2)))
    sh=df[df["sh"]]["High"]; sl=df[df["sl"]]["Low"]
    if len(sh)<2 or len(sl)<2: return {"romp":False,"choch":False,"tendencia":"NEUTRO","ush":0,"usl":0,"sh_list":[],"sl_list":[]}
    ush=sh.iloc[-1]; psh=sh.iloc[-2]; usl=sl.iloc[-1]; psl=sl.iloc[-2]
    p=df["Close"].iloc[-1]; pp=df["Close"].iloc[-2]
    bb=(p>ush)and(pp>ush); bs=(p<usl)and(pp<usl)
    hh=ush>psh; hl=usl>psl; lh=ush<psh; ll=usl<psl
    t="BULLISH" if(hh and hl)else "BEARISH" if(lh and ll)else "NEUTRO"
    ch=((hh and hl)and bs)or((lh and ll)and bb)
    return {"romp":bb or bs,"choch":ch,"tendencia":t,"ush":round(float(ush),5),"usl":round(float(usl),5),"sh_list":list(sh.tail(3).values),"sl_list":list(sl.tail(3).values)}

def detectar_sweep(df, est):
    if not est["sh_list"] or not est["sl_list"]: return False,False
    p=float(df["Close"].iloc[-1])
    h3=df["High"].tail(3); l3=df["Low"].tail(3)
    ush=float(est["sh_list"][-1]); usl=float(est["sl_list"][-1])
    return (float(l3.min())<usl)and(p>usl),(float(h3.max())>ush)and(p<ush)

def detectar_smc(df, dr):
    if len(df)<25: return {"ob":None,"fvg":False,"fvg_range":None}
    df=df.copy()
    df["vf"]=df["corpo"]>df["media_corpo"]*1.5
    ob=None
    for i in range(len(df)-3,max(len(df)-25,0),-1):
        if df["vf"].iloc[i]:
            if dr=="BUY" and df["Close"].iloc[i]<df["Open"].iloc[i]:
                if df["Close"].iloc[-1]>df["High"].iloc[i]: ob=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
            elif dr=="SELL" and df["Close"].iloc[i]>df["Open"].iloc[i]:
                if df["Close"].iloc[-1]<df["Low"].iloc[i]: ob=(round(float(df["Low"].iloc[i]),5),round(float(df["High"].iloc[i]),5)); break
    fvg=False; fvg_range=None
    for i in range(len(df)-2,max(len(df)-20,1),-1):
        if i+1>=len(df): continue
        if dr=="BUY" and df["Low"].iloc[i+1]>df["High"].iloc[i-1]:
            fvg=True; fvg_range=(round(float(df["High"].iloc[i-1]),5),round(float(df["Low"].iloc[i+1]),5)); break
        elif dr=="SELL" and df["High"].iloc[i+1]<df["Low"].iloc[i-1]:
            fvg=True; fvg_range=(round(float(df["High"].iloc[i+1]),5),round(float(df["Low"].iloc[i-1]),5)); break
    return {"ob":ob,"fvg":fvg,"fvg_range":fvg_range}

def detectar_reteste(df, smc, dr):
    p=float(df["Close"].iloc[-1])
    if smc["ob"]:
        if dr=="BUY" and smc["ob"][0]<=p<=smc["ob"][1]*1.002: return True,"Reteste OB"
        if dr=="SELL" and smc["ob"][0]*0.998<=p<=smc["ob"][1]: return True,"Reteste OB"
    if smc["fvg_range"]:
        lo,hi=smc["fvg_range"]
        if dr=="BUY" and lo<=p<=hi*1.002: return True,"Reteste FVG"
        if dr=="SELL" and lo*0.998<=p<=hi: return True,"Reteste FVG"
    return False,""

def tendencia_tf(par, intervalo, periodo):
    try:
        d=obter_dados(par,intervalo=intervalo,periodo=periodo)
        if len(d)<50: return "NEUTRO"
        d=adicionar_indicadores(d)
        if len(d)<3: return "NEUTRO"
        e20=float(d["ema20"].iloc[-1]); e50=float(d["ema50"].iloc[-1]); p=float(d["Close"].iloc[-1])
        del d; gc.collect()
        if p>e20>e50: return "BULLISH"
        elif p<e20<e50: return "BEARISH"
        else: return "NEUTRO"
    except: return "NEUTRO"

def vela_confirmacao_m5(par, dr):
    try:
        d=obter_dados(par,intervalo="5m",periodo="1d")
        if len(d)<5: return False
        d=adicionar_indicadores(d)
        if len(d)<3: return False
        ultima=d.iloc[-1]
        corpo=float(ultima["corpo"]); media=float(ultima["media_corpo"])
        if dr=="BUY":
            bullish=float(ultima["Close"])>float(ultima["Open"])
            forte=corpo>media*1.2
            result=bullish and forte
        else:
            bearish=float(ultima["Close"])<float(ultima["Open"])
            forte=corpo>media*1.2
            result=bearish and forte
        del d; gc.collect()
        return result
    except: return False

def tipo_ordem(sweep, reteste, dr):
    if reteste: return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"
    elif sweep: return "MARKET ORDER"
    else: return "BUY STOP" if dr=="BUY" else "SELL STOP"

def calc_risco(preco, dr, atr, rsi, par):
    p=float(preco); a=float(atr)
    eh_ouro="GC" in par or "XAU" in par
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
    pips=round(r,2) if eh_ouro else round(r*10000,1)
    unidade="USD" if eh_ouro else "pips"
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":pips,"unidade":unidade,"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def classificar(score):
    if score>=95: return "PREMIUM","🔥"
    elif score>=90: return "EXCELENTE","⭐"
    elif score>=80: return "BOM","✅"
    else: return None,""

def analisar(par, ignorar_sessao=False):
    try:
        d15=obter_dados(par,"15m","3d")
        if len(d15)<80: return None
        d15=adicionar_indicadores(d15)
        if len(d15)<10: return None
        s=sessao_activa()
        sessao_ok=s["operar"] if not ignorar_sessao else True
        preco=round(float(d15["Close"].iloc[-1]),5)
        rsi=round(float(d15["rsi"].iloc[-1]),1)
        atr=float(d15["atr"].iloc[-1]); atr_med=float(d15["atr"].tail(50).mean())
        atr_ok=atr>=atr_med*0.7
        nome=par.replace("=X","").replace("=F","")
        is_range,mot_range=detectar_range(d15)
        if is_range: 
            del d15; gc.collect()
            return {"par":nome,"sinal":False,"bloqueado":True,"motivo":"Range: "+" ".join(mot_range),"score":0,"dir":"","preco":preco,"rsi":rsi,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","tend_h1":"","tend_m15":"","tend_m5":"","criterios":{},"raz":[],"sessao":s["sessao"],"operar":s["operar"]}
        est15=detectar_estrutura(d15)
        if not est15["romp"] and not est15["choch"]:
            del d15; gc.collect()
            return {"par":nome,"sinal":False,"bloqueado":True,"motivo":"Sem BOS/CHoCH","score":0,"dir":"","preco":preco,"rsi":rsi,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","tend_h1":"","tend_m15":est15["tendencia"],"tend_m5":"","criterios":{},"raz":[],"sessao":s["sessao"],"operar":s["operar"]}
        p_val=float(d15["Close"].iloc[-1])
        e20=float(d15["ema20"].iloc[-1]); e50=float(d15["ema50"].iloc[-1]); e200=float(d15["ema200"].iloc[-1])
        cb=sum([p_val>e20,p_val>e50,p_val>e200,e20>e50>e200,est15["tendencia"]=="BULLISH"])
        cs=sum([p_val<e20,p_val<e50,p_val<e200,e20<e50<e200,est15["tendencia"]=="BEARISH"])
        dr="BUY" if cb>=cs else "SELL"
        rsi_ok=rsi<=70 if dr=="BUY" else rsi>=30
        if not rsi_ok:
            del d15; gc.collect()
            return {"par":nome,"sinal":False,"bloqueado":True,"motivo":f"RSI {rsi} extremo","score":0,"dir":dr,"preco":preco,"rsi":rsi,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","tend_h1":"","tend_m15":est15["tendencia"],"tend_m5":"","criterios":{},"raz":[],"sessao":s["sessao"],"operar":s["operar"]}
        sw_bull,sw_bear=detectar_sweep(d15,est15)
        sw_ok=sw_bull if dr=="BUY" else sw_bear
        smc15=detectar_smc(d15,dr)
        reteste,mot_ret=detectar_reteste(d15,smc15,dr)
        ema200_ok=(p_val>e200 and dr=="BUY") or (p_val<e200 and dr=="SELL")
        tend_h1=tendencia_tf(par,"1h","5d")
        h1_ok=tend_h1==est15["tendencia"] and tend_h1!="NEUTRO"
        tend_m5=tendencia_tf(par,"5m","1d")
        m5_ok=tend_m5==dr
        vela_m5=vela_confirmacao_m5(par,dr)
        criterios={
            "BOS/CHoCH M15": est15["romp"] or est15["choch"],
            "Liquidity Sweep": sw_ok,
            "Reteste OB/FVG": reteste,
            "EMA200 alinhada": ema200_ok,
            "H1 confirma": h1_ok,
            "M5 confirma": m5_ok,
            "Vela M5 confirmacao": vela_m5,
            "ATR suficiente": atr_ok,
            "Sessao activa": sessao_ok
        }
        todos_ok=all(criterios.values())
        score=0; raz=[]
        if s["operar"]: score+=s["score_bonus"]; raz.append(f"Sessao {s['sessao']}")
        if est15["romp"]: score+=20; raz.append("Rompimento M15 +20")
        if est15["choch"]: score+=5; raz.append("CHoCH +5")
        if sw_ok: score+=15; raz.append("Liquidity Sweep +15")
        if reteste: score+=15; raz.append(f"{mot_ret} +15")
        if h1_ok: score+=10; raz.append(f"H1 {tend_h1} +10")
        if m5_ok: score+=10; raz.append(f"M5 {tend_m5} +10")
        if vela_m5: score+=10; raz.append("Vela confirmacao M5 +10")
        if smc15["ob"]: score+=5; raz.append("OB +5")
        if smc15["fvg"]: score+=5; raz.append("FVG +5")
        if rsi_ok: score+=5; raz.append(f"RSI {rsi} +5")
        r=calc_risco(preco,dr,atr,rsi,par)
        ordem=tipo_ordem(sw_ok,reteste,dr)
        classificacao,emoji=classificar(score)
        sinal=todos_ok and score>=80 and r["pips"]>0
        aprovados=sum(1 for v in criterios.values() if v)
        result={"par":nome,"dir":dr,"score":score,"classificacao":classificacao,"emoji":emoji,"sinal":sinal,"todos_ok":todos_ok,"preco":preco,"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],"rsi":rsi,"tend_h1":tend_h1,"tend_m15":est15["tendencia"],"tend_m5":tend_m5,"sweep":sw_ok,"reteste":reteste,"ema200_ok":ema200_ok,"atr_ok":atr_ok,"criterios":criterios,"aprovados":aprovados,"ordem":ordem,"sessao":s["sessao"],"operar":s["operar"],"raz":raz,"est":est15,"smc":smc15}
        del d15; gc.collect()
        return result
    except: 
        gc.collect()
        return None

def grafico_simples(par, est, smc, dr):
    try:
        d=obter_dados(par,"15m","2d")
        if len(d)<10: return None
        d=adicionar_indicadores(d)
        df=d.tail(60)
        fig=go.Figure(go.Candlestick(x=df.index,open=df["Open"],high=df["High"],low=df["Low"],close=df["Close"],name=par,increasing_line_color="#26a69a",decreasing_line_color="#ef5350"))
        if "ema20" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
        if "ema50" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
        if "ema200" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema200"],name="EMA200",line=dict(color="red",width=1)))
        if est.get("ush"): fig.add_hline(y=est["ush"],line_dash="dash",line_color="red",annotation_text="Swing High")
        if est.get("usl"): fig.add_hline(y=est["usl"],line_dash="dash",line_color="green",annotation_text="Swing Low")
        if smc.get("ob"): fig.add_hrect(y0=smc["ob"][0],y1=smc["ob"][1],fillcolor="rgba(38,166,154,0.15)",line_width=0,annotation_text="OB")
        fig.update_layout(title=f"{par} M15",xaxis_rangeslider_visible=False,height=400,template="plotly_dark",showlegend=False)
        del d; gc.collect()
        return fig
    except: return None

def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK: return
        resend.api_key=os.environ.get("RESEND_API_KEY","")
        email_destino=os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        crit_txt=chr(10).join([("OK  " if v else "NOK ") + k for k,v in sinal["criterios"].items()])
        corpo=(
            sinal["emoji"]+" SINAL SMC PRO v7 — "+str(sinal["classificacao"])+chr(10)+
            "================================"+chr(10)+
            "Par:           "+sinal["par"]+chr(10)+
            "Direccao:      "+sinal["dir"]+chr(10)+
            "Tipo Ordem:    "+sinal["ordem"]+chr(10)+
            "Preco Entrada: "+str(sinal["preco"])+chr(10)+
            "Score:         "+str(sinal["score"])+"% — "+str(sinal["classificacao"])+chr(10)+
            "RSI:           "+str(sinal["rsi"])+chr(10)+
            "H1:  "+sinal["tend_h1"]+" | M15: "+sinal["tend_m15"]+" | M5: "+sinal["tend_m5"]+chr(10)+chr(10)+
            "9 CRITERIOS ("+str(sinal["aprovados"])+"/9)"+chr(10)+
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
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":sinal["emoji"]+" v7: "+sinal["par"]+" "+sinal["dir"]+" | "+sinal["ordem"]+" @ "+str(sinal["preco"])+" | "+str(sinal["score"])+"% "+str(sinal["classificacao"]),"text":corpo})
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

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started=True
    th=threading.Thread(target=monitor_background,daemon=True)
    th.start()

st.title("SMC Signals Pro v7")
st.caption("H1 + M15 + M5 | 9 criterios obrigatorios | Qualidade > Quantidade | Monitor 24/7")
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
        st.success(str(len(sinais))+" SINAL(IS) CONFIRMADO(S)!")
        for s in sinais:
            with st.expander(s["emoji"]+" "+s["par"]+" "+s["dir"]+" | "+s["ordem"]+" @ "+str(s["preco"])+" | "+str(s["score"])+"%",expanded=True):
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Preco Entrada",s["preco"])
                c3.metric("Score",str(s["score"])+"%")
                c4.metric("Classe",str(s["classificacao"]))
                c1b,c2b,c3b,c4b=st.columns(4)
                c1b.metric("Stop Loss",s["sl"])
                c2b.metric("TP1",s["tp1"])
                c3b.metric("TP2",s["tp2"])
                c4b.metric("Risco",str(s["pips"])+" "+s["unidade"])
                c1c,c2c,c3c=st.columns(3)
                c1c.metric("H1",s["tend_h1"])
                c2c.metric("M15",s["tend_m15"])
                c3c.metric("M5",s["tend_m5"])
                st.markdown("**Criterios:**")
                cols=st.columns(3)
                for idx,(k,v) in enumerate(s["criterios"].items()):
                    cols[idx%3].write(("✅ " if v else "❌ ")+k)
                fig=grafico_simples(s["par"],s["est"],s["smc"],s["dir"])
                if fig: st.plotly_chart(fig,use_container_width=True)
    else:
        st.info("Sem sinais. Sistema aguarda confluencia H1+M15+M5.")

    if resultados:
        st.subheader("Todos os pares")
        linhas=[]
        for r in resultados:
            if r.get("sinal"): status=r.get("emoji","")+" "+str(r.get("classificacao",""))
            elif r.get("bloqueado"): status="BLOQ: "+r.get("motivo","")[:15]
            elif r.get("aprovados",0)>=6: status="QUASE"
            else: status="AGUARDA"
            linhas.append({"Status":status,"Par":r["par"],"Dir":r["dir"],"Entrada":r["preco"],"Score":str(r["score"])+"%","RSI":r["rsi"],"H1":r.get("tend_h1",""),"M15":r.get("tend_m15",""),"M5":r.get("tend_m5",""),"Crit":str(r.get("aprovados",0))+"/9","SL":r["sl"],"TP1":r["tp1"],"TP2":r["tp2"]})
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | Score>=80% + 9 criterios | PREMIUM>=95 EXCELENTE>=90 BOM>=80")
