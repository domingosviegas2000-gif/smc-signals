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

st.set_page_config(page_title="SMC Signals Pro v6", page_icon="📈", layout="wide")
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
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO LONDRES+NY","qualidade":"EXCELENTE","score_bonus":20,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","qualidade":"BOA","score_bonus":15,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","qualidade":"BOA","score_bonus":15,"operar":True}
    else: return {"sessao":"ASIATICA","qualidade":"FRACA","score_bonus":0,"operar":False}

def detectar_range(df):
    e20=float(df["ema20"].iloc[-1]); e50=float(df["ema50"].iloc[-1])
    atr=float(df["atr"].iloc[-1]); atr_med=float(df["atr"].tail(50).mean())
    emas_proximas=abs(e20-e50)/e50 < 0.0003
    atr_baixo=atr < atr_med*0.7
    highs=df["High"].tail(20); lows=df["Low"].tail(20)
    hh=highs.iloc[-1]>highs.iloc[-5]>highs.iloc[-10]
    ll=lows.iloc[-1]<lows.iloc[-5]<lows.iloc[-10]
    hl=lows.iloc[-1]>lows.iloc[-5]>lows.iloc[-10]
    lh=highs.iloc[-1]<highs.iloc[-5]<highs.iloc[-10]
    estrutura_clara=hh or ll or hl or lh
    velas_pequenas=(df["corpo"].tail(10)<df["media_corpo"].tail(10)*0.5).sum()>=7
    is_range=emas_proximas or atr_baixo or not estrutura_clara or velas_pequenas
    motivos=[]
    if emas_proximas: motivos.append("EMAs proximas")
    if atr_baixo: motivos.append("ATR baixo")
    if not estrutura_clara: motivos.append("Sem estrutura clara")
    if velas_pequenas: motivos.append("Velas pequenas")
    return is_range, motivos

def detectar_estrutura(df):
    df=df.copy()
    df["sh"]=((df["High"]>df["High"].shift(1))&(df["High"]>df["High"].shift(-1))&(df["High"]>df["High"].shift(2))&(df["High"]>df["High"].shift(-2))&(df["High"]>df["High"].shift(3))&(df["High"]>df["High"].shift(-3)))
    df["sl"]=((df["Low"]<df["Low"].shift(1))&(df["Low"]<df["Low"].shift(-1))&(df["Low"]<df["Low"].shift(2))&(df["Low"]<df["Low"].shift(-2))&(df["Low"]<df["Low"].shift(3))&(df["Low"]<df["Low"].shift(-3)))
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
    return {"sweep_bull":(float(l5.min())<float(usl))and(p>float(usl)),"sweep_bear":(float(h5.max())>float(ush))and(p<float(ush))}

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
    suporte_proximo=abs(p-float(lf))/float(lf)<0.0005
    resistencia_proxima=abs(p-float(lt))/float(lt)<0.0005
    return {"ob_bull":ob_bull,"ob_bear":ob_bear,"fvg_bull":fb,"fvg_bear":fs,"fbr":fbr,"fsr":fsr,"lt":lt,"lf":lf,"ct":abs(p-float(lt))/float(lt)<0.0008,"cf":abs(p-float(lf))/float(lf)<0.0008,"suporte_proximo":suporte_proximo,"resistencia_proxima":resistencia_proxima}

def detectar_reteste(df, smc, dr):
    p=float(df["Close"].iloc[-1])
    if dr=="BUY":
        if smc["ob_bull"] and smc["ob_bull"][0]<=p<=smc["ob_bull"][1]*1.001: return True,"Reteste OB Bull"
        if smc["fbr"] and smc["fbr"][0]<=p<=smc["fbr"][1]*1.001: return True,"Reteste FVG Bull"
    else:
        if smc["ob_bear"] and smc["ob_bear"][0]*0.999<=p<=smc["ob_bear"][1]: return True,"Reteste OB Bear"
        if smc["fsr"] and smc["fsr"][0]*0.999<=p<=smc["fsr"][1]: return True,"Reteste FVG Bear"
    return False,""

def tendencia_h1(par):
    try:
        d=obter_dados(par,intervalo="1h",periodo="10d")
        d=adicionar_indicadores(d)
        e20=float(d["ema20"].iloc[-1]); e50=float(d["ema50"].iloc[-1]); p=float(d["Close"].iloc[-1])
        if p>e20>e50: return "BULLISH"
        elif p<e20<e50: return "BEARISH"
        else: return "NEUTRO"
    except: return "NEUTRO"

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

def classificar_sinal(score):
    if score>=95: return "PREMIUM","🔥"
    elif score>=90: return "EXCELENTE","⭐"
    elif score>=80: return "BOM","✅"
    else: return None,""

def analisar(par, ignorar_sessao=False):
    try:
        d=obter_dados(par)
        if len(d)<100: return None
        d=adicionar_indicadores(d)
        s=sessao_activa()
        preco=round(float(d["Close"].iloc[-1]),5)
        rsi=round(float(d["rsi"].iloc[-1]),1)
        nome=par.replace("=X","").replace("=F","")
        atr=float(d["atr"].iloc[-1]); atr_med=float(d["atr"].tail(50).mean())
        atr_ok=atr>=atr_med*0.7
        sessao_ok=s["operar"] if not ignorar_sessao else True
        is_range,motivos_range=detectar_range(d)
        est=detectar_estrutura(d)
        smc=detectar_smc(d)
        sweep=detectar_sweep(d,est)
        t_h1=tendencia_h1(par)
        p_val=float(d["Close"].iloc[-1])
        e20=float(d["ema20"].iloc[-1]); e50=float(d["ema50"].iloc[-1]); e200=float(d["ema200"].iloc[-1])
        cb=[]; cs=[]
        if p_val>e20: cb.append("Acima EMA20")
        else: cs.append("Abaixo EMA20")
        if p_val>e50: cb.append("Acima EMA50")
        else: cs.append("Abaixo EMA50")
        if p_val>e200: cb.append("Acima EMA200")
        else: cs.append("Abaixo EMA200")
        if e20>e50>e200: cb.append("EMAs Bull")
        elif e20<e50<e200: cs.append("EMAs Bear")
        if est["tendencia"]=="BULLISH": cb.append("Estrutura Bull")
        elif est["tendencia"]=="BEARISH": cs.append("Estrutura Bear")
        dr="BUY" if len(cb)>=len(cs) else "SELL"
        reteste,mot_ret=detectar_reteste(d,smc,dr)
        ema200_ok=(p_val>e200 and dr=="BUY") or (p_val<e200 and dr=="SELL")
        h1_ok=t_h1==est["tendencia"] and t_h1!="NEUTRO"
        sw_ok=sweep["sweep_bull"] if dr=="BUY" else sweep["sweep_bear"]
        bos_ok=est["romp"] or est["choch"]
        rsi_ok=rsi<=70 if dr=="BUY" else rsi>=30
        resistencia_ok=not smc["resistencia_proxima"] if dr=="BUY" else not smc["suporte_proximo"]

        # 7 CRITERIOS OBRIGATORIOS
        criterios = {
            "BOS/CHoCH confirmado": bos_ok,
            "Liquidity Sweep": sw_ok,
            "Reteste OB/FVG": reteste,
            "EMA200 alinhada": ema200_ok,
            "H1 confirma": h1_ok,
            "ATR suficiente": atr_ok,
            "Sessao activa": sessao_ok
        }

        # FILTROS ADICIONAIS
        filtros = {
            "RSI valido": rsi_ok,
            "Sem range": not is_range,
            "Sem resistencia proxima": resistencia_ok
        }

        todos_criterios=all(criterios.values())
        todos_filtros=all(filtros.values())
        pode_sinal=todos_criterios and todos_filtros

        # PONTUACAO
        score=0; raz=[]
        if s["operar"]: score+=s["score_bonus"]; raz.append(f"Sessao {s['sessao']}")
        if est["romp"]: score+=20; raz.append("Rompimento confirmado +20")
        if est["choch"]: score+=5; raz.append("CHoCH +5")
        if reteste: score+=15; raz.append(f"{mot_ret} +15")
        if sw_ok: score+=15; raz.append("Liquidity Sweep +15")
        if dr=="BUY" and smc["ob_bull"]: score+=10; raz.append("OB Bull +10")
        if dr=="SELL" and smc["ob_bear"]: score+=10; raz.append("OB Bear +10")
        if dr=="BUY" and smc["fvg_bull"]: score+=10; raz.append("FVG Bull +10")
        if dr=="SELL" and smc["fvg_bear"]: score+=10; raz.append("FVG Bear +10")
        if h1_ok: score+=10; raz.append(f"H1 {t_h1} +10")
        if rsi_ok: score+=5; raz.append(f"RSI {rsi} +5")
        if atr_ok: score+=5; raz.append("ATR +5")

        classificacao,emoji=classificar_sinal(score)
        ordem=tipo_ordem(sw_ok,reteste,dr)
        r=calc_risco(preco,dr,atr,rsi,par)
        aprovados_c=sum(1 for v in criterios.values() if v)
        aprovados_f=sum(1 for v in filtros.values() if v)
        sinal=pode_sinal and score>=80 and r["pips"]>0

        return {"par":nome,"dir":dr,"score":score,"classificacao":classificacao,"emoji":emoji,"sinal":sinal,"pode_sinal":pode_sinal,"preco":preco,"sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],"rsi":rsi,"tend":est["tendencia"],"tend_h1":t_h1,"sweep":sw_ok,"reteste":reteste,"ema200_ok":ema200_ok,"atr_ok":atr_ok,"criterios":criterios,"filtros":filtros,"aprovados_c":aprovados_c,"aprovados_f":aprovados_f,"ordem":ordem,"is_range":is_range,"motivos_range":motivos_range,"sessao":s["sessao"],"operar":s["operar"],"raz":raz,"dados":d,"est":est,"smc":smc}
    except: return None

def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK: return
        resend.api_key=os.environ.get("RESEND_API_KEY","")
        email_destino=os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        crit_txt=chr(10).join([("OK  " if v else "NOK ") + k for k,v in sinal["criterios"].items()])
        filt_txt=chr(10).join([("OK  " if v else "NOK ") + k for k,v in sinal["filtros"].items()])
        corpo=(
            sinal["emoji"]+" SINAL SMC PRO v6 — "+sinal["classificacao"]+" "+chr(10)+
            "================================"+chr(10)+
            "Par:           "+sinal["par"]+chr(10)+
            "Direccao:      "+sinal["dir"]+chr(10)+
            "Tipo Ordem:    "+sinal["ordem"]+chr(10)+
            "Preco Entrada: "+str(sinal["preco"])+chr(10)+
            "Classificacao: "+str(sinal["score"])+"% — "+str(sinal["classificacao"])+chr(10)+
            "RSI:           "+str(sinal["rsi"])+chr(10)+
            "M15:           "+sinal["tend"]+" | H1: "+sinal["tend_h1"]+chr(10)+chr(10)+
            "7 CRITERIOS OBRIGATORIOS ("+str(sinal["aprovados_c"])+"/7)"+chr(10)+
            "================================"+chr(10)+
            crit_txt+chr(10)+chr(10)+
            "FILTROS ADICIONAIS ("+str(sinal["aprovados_f"])+"/3)"+chr(10)+
            "================================"+chr(10)+
            filt_txt+chr(10)+chr(10)+
            "GESTAO DE RISCO"+chr(10)+
            "================================"+chr(10)+
            "SL:    "+str(sinal["sl"])+chr(10)+
            "TP1:   "+str(sinal["tp1"])+chr(10)+
            "TP2:   "+str(sinal["tp2"])+chr(10)+
            "R:R:   "+sinal["rr"]+chr(10)+
            "Risco: "+str(sinal["pips"])+" "+sinal["unidade"]+chr(10)+chr(10)+
            "CONFIRMACOES"+chr(10)+
            "================================"+chr(10)+
            chr(10).join(sinal["raz"])
        )
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":sinal["emoji"]+" SINAL v6: "+sinal["par"]+" "+sinal["dir"]+" | "+sinal["ordem"]+" @ "+str(sinal["preco"])+" | "+str(sinal["score"])+"% "+str(sinal["classificacao"]),"text":corpo})
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

st.title("SMC Signals Pro v6")
st.caption("7 criterios obrigatorios + 3 filtros | Qualidade > Quantidade | Monitor 24/7")
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
        st.success(str(len(sinais))+" SINAL(IS) DE ALTA QUALIDADE!")
        for s in sinais:
            with st.expander(s["emoji"]+" SINAL "+s["classificacao"]+": "+s["par"]+" "+s["dir"]+" | "+s["ordem"]+" @ "+str(s["preco"])+" | "+str(s["score"])+"%",expanded=True):
                st.markdown("### "+s["emoji"]+" "+s["par"]+" — "+s["dir"]+" — "+str(s["classificacao"]))
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Preco Entrada",s["preco"])
                c3.metric("Score",str(s["score"])+"%")
                c4.metric("Classificacao",str(s["classificacao"]))
                c1b,c2b,c3b,c4b=st.columns(4)
                c1b.metric("Stop Loss",s["sl"])
                c2b.metric("TP1",s["tp1"])
                c3b.metric("TP2",s["tp2"])
                c4b.metric("Risco",str(s["pips"])+" "+s["unidade"])
                col_a,col_b=st.columns(2)
                with col_a:
                    st.markdown("**7 Criterios Obrigatorios:**")
                    for k,v in s["criterios"].items():
                        st.write(("✅ " if v else "❌ ")+k)
                with col_b:
                    st.markdown("**Filtros Adicionais:**")
                    for k,v in s["filtros"].items():
                        st.write(("✅ " if v else "❌ ")+k)
                st.caption("M15: "+s["tend"]+" | H1: "+s["tend_h1"]+" | RSI: "+str(s["rsi"])+" | R:R "+s["rr"])
                st.caption(" | ".join(s["raz"]))
                st.plotly_chart(grafico(s["dados"],s["est"],s["smc"],s["par"]),use_container_width=True)
    else:
        st.info("Sistema aguarda confluencia perfeita. Qualidade acima de quantidade.")

    if resultados:
        st.subheader("Todos os pares — Estado actual")
        linhas=[]
        for r in resultados:
            if r.get("sinal"): status=r.get("emoji","")+" "+str(r.get("classificacao",""))
            elif r.get("aprovados_c",0)>=5: status="QUASE"
            elif r.get("is_range"): status="RANGE"
            else: status="AGUARDA"
            linhas.append({"Status":status,"Par":r["par"],"Dir":r["dir"],"Ordem":r.get("ordem",""),"Entrada":r["preco"],"Score":str(r["score"])+"%","RSI":r["rsi"],"Crit":str(r.get("aprovados_c",0))+"/7","M15":r["tend"],"H1":r.get("tend_h1",""),"BOS":"S" if r.get("est",{}).get("romp") else "N","Sweep":"S" if r.get("sweep") else "N","Reteste":"S" if r.get("reteste") else "N","EMA200":"S" if r.get("ema200_ok") else "N","SL":r["sl"],"TP1":r["tp1"],"TP2":r["tp2"]})
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | Score>=80% + 7 criterios + 3 filtros | PREMIUM>=95 EXCELENTE>=90 BOM>=80")
