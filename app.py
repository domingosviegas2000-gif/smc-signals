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
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","score_bonus":20,"operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","score_bonus":15,"operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","score_bonus":15,"operar":True}
    else: return {"sessao":"ASIATICA","score_bonus":0,"operar":False}

# FASE 1: Estrutura H1 — tendencia geral
def fase1_estrutura_h1(par):
    try:
        d=obter_dados(par,"1h","5d")
        if len(d)<20: return "NEUTRO"
        d=adicionar_indicadores(d)
        if len(d)<5: return "NEUTRO"
        e20=float(d["ema20"].iloc[-1])
        e50=float(d["ema50"].iloc[-1])
        p=float(d["Close"].iloc[-1])
        del d; gc.collect()
        if p>e20>e50: return "BULLISH"
        elif p<e20<e50: return "BEARISH"
        else: return "NEUTRO"
    except: return "NEUTRO"

# FASE 2: Liquidez M15 — captura de topos e fundos
def fase2_liquidez_m15(df):
    try:
        df2=df.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
        sh=df2[df2["sh"]]["High"]
        sl=df2[df2["sl"]]["Low"]
        if len(sh)<2 or len(sl)<2: return False,False,0,0
        p=float(df["Close"].iloc[-1])
        h3=df["High"].tail(5)
        l3=df["Low"].tail(5)
        ush=float(sh.iloc[-1])
        usl=float(sl.iloc[-1])
        # Liquidez bull: preco varreu fundo e voltou acima
        liq_bull=(float(l3.min())<usl)and(p>usl)
        # Liquidez bear: preco varreu topo e voltou abaixo
        liq_bear=(float(h3.max())>ush)and(p<ush)
        return liq_bull,liq_bear,round(usl,5),round(ush,5)
    except: return False,False,0,0

# FASE 3: BOS/CHoCH M15 — rompimento de estrutura
def fase3_bos_choch_m15(df):
    try:
        df2=df.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2))&(df2["High"]>df2["High"].shift(3))&(df2["High"]>df2["High"].shift(-3)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2))&(df2["Low"]<df2["Low"].shift(3))&(df2["Low"]<df2["Low"].shift(-3)))
        sh=df2[df2["sh"]]["High"]
        sl=df2[df2["sl"]]["Low"]
        if len(sh)<3 or len(sl)<3: return False,False,"NEUTRO"
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
        tipo="CHoCH" if choch_ok else "BOS" if bos_ok else "NENHUM"
        return bos_ok,choch_ok,t,tipo
    except: return False,False,"NEUTRO","NENHUM"

# FASE 4: Volume — confirma forca do movimento
def fase4_volume(df, par):
    try:
        eh_ouro="GC" in par
        ultima=df.iloc[-1]
        vol=float(ultima["Volume"])
        vol_med=float(ultima["vol_medio"]) if float(ultima["vol_medio"])>0 else 1
        corpo=float(ultima["corpo"])
        media=float(ultima["media_corpo"])
        if eh_ouro:
            # Ouro: usa forca do corpo (volume pouco fiavel)
            if corpo>media*1.5: return "FORTE",True
            elif corpo>media*0.8: return "MEDIO",False
            else: return "FRACO",False
        else:
            if vol>vol_med*1.3 and corpo>media*1.2: return "FORTE",True
            elif vol>vol_med*0.7: return "MEDIO",False
            else: return "FRACO",False
    except: return "MEDIO",False

# FASE 5: Reteste M5 — confirmacao final de entrada
def fase5_reteste_m5(par, dr, nivel_liq):
    try:
        d=obter_dados(par,"5m","1d")
        if len(d)<15: return False,"SEM_DADOS",0,0,0
        d=adicionar_indicadores(d)
        if len(d)<10: return False,"SEM_DADOS",0,0,0

        preco_m5=round(float(d["Close"].iloc[-1]),5)
        high_m5=round(float(d["High"].iloc[-1]),5)
        low_m5=round(float(d["Low"].iloc[-1]),5)
        ultima=d.iloc[-1]
        corpo=float(ultima["corpo"])
        media=float(ultima["media_corpo"])
        e20=float(d["ema20"].iloc[-1])
        atr_m5=float(d["atr"].iloc[-1])

        # Verifica se preco esta proximo do nivel de liquidez
        if nivel_liq>0:
            distancia=abs(preco_m5-nivel_liq)/nivel_liq
            proximo_nivel=distancia<0.002  # 0.2% de distancia
        else:
            proximo_nivel=True

        # Vela de rejeicao no M5
        if dr=="BUY":
            vela_bull=float(ultima["Close"])>float(ultima["Open"])
            corpo_forte=corpo>media*1.0
            acima_e20=preco_m5>e20
            rejeicao=vela_bull and corpo_forte
            tipo_reteste="REJEICAO_BULL" if rejeicao else "SEM_RETESTE"
        else:
            vela_bear=float(ultima["Close"])<float(ultima["Open"])
            corpo_forte=corpo>media*1.0
            abaixo_e20=preco_m5<e20
            rejeicao=vela_bear and corpo_forte
            tipo_reteste="REJEICAO_BEAR" if rejeicao else "SEM_RETESTE"

        # BOS no M5 — rompimento de estrutura menor
        df2=d.copy()
        df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&(df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
        df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&(df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
        sh_m5=df2[df2["sh"]]["High"]
        sl_m5=df2[df2["sl"]]["Low"]
        bos_m5=False
        if len(sh_m5)>=2 and len(sl_m5)>=2:
            ush_m5=float(sh_m5.iloc[-1]); usl_m5=float(sl_m5.iloc[-1])
            p=float(d["Close"].iloc[-1]); pp=float(d["Close"].iloc[-2])
            if dr=="BUY": bos_m5=(p>ush_m5)and(pp>ush_m5)
            else: bos_m5=(p<usl_m5)and(pp<usl_m5)
            if bos_m5: tipo_reteste="BOS_M5"

        reteste_ok=rejeicao or bos_m5
        del d,df2; gc.collect()
        return reteste_ok,tipo_reteste,preco_m5,high_m5,low_m5
    except:
        gc.collect()
        return False,"ERRO",0,0,0

# Tipo de ordem automatico
def tipo_ordem_auto(reteste_ok, tipo_reteste, vol_forte, liq_ok, dr):
    if tipo_reteste=="BOS_M5" or tipo_reteste in ["REJEICAO_BULL","REJEICAO_BEAR"]:
        # Reteste ja confirmado no M5 — entra a mercado
        return "MARKET ORDER"
    elif liq_ok and not reteste_ok:
        # Liquidez capturada mas sem reteste ainda — espera reteste
        return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"
    elif vol_forte and not reteste_ok:
        # Impulso forte sem pullback — entra no rompimento
        return "BUY STOP" if dr=="BUY" else "SELL STOP"
    else:
        return "BUY LIMIT" if dr=="BUY" else "SELL LIMIT"

# Gestao de risco baseada no M5
def calc_risco(preco, dr, high_m5, low_m5, atr, par):
    p=float(preco); a=float(atr)
    eh_ouro="GC" in par
    mult=1.0 if eh_ouro else 0.5
    if dr=="BUY":
        sl=round(low_m5-a*mult,5) if low_m5>0 else round(p-a*1.5,5)
        if sl>=p: sl=round(p-a,5)
        risco=abs(p-sl)
        tp1=round(p+risco*2,5)
        tp2=round(p+risco*4,5)
    else:
        sl=round(high_m5+a*mult,5) if high_m5>0 else round(p+a*1.5,5)
        if sl<=p: sl=round(p+a,5)
        risco=abs(sl-p)
        tp1=round(p-risco*2,5)
        tp2=round(p-risco*4,5)
    r=abs(p-sl)
    pips=round(r,2) if eh_ouro else round(r*10000,1)
    unidade="USD" if eh_ouro else "pips"
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"pips":pips,"unidade":unidade,"rr":f"1:{round(abs(p-tp2)/r,1) if r>0 else 0}"}

def classificar(score):
    if score>=85: return "PREMIUM","🟢"
    elif score>=70: return "NORMAL","🟡"
    elif score>=60: return "ACEITAVEL","🔵"
    else: return "FRACO","⚫"

# MOTOR PRINCIPAL
def analisar(par, ignorar_sessao=False):
    try:
        nome=nomes.get(par,par.replace("=X","").replace("=F",""))
        s=sessao_activa()
        sessao_ok=s["operar"] if not ignorar_sessao else True

        # Carrega M15
        d15=obter_dados(par,"15m","3d")
        if len(d15)<50:
            del d15; gc.collect()
            return None
        d15=adicionar_indicadores(d15)
        if len(d15)<10:
            del d15; gc.collect()
            return None

        preco_m15=round(float(d15["Close"].iloc[-1]),5)
        rsi=round(float(d15["rsi"].iloc[-1]),1)
        atr=float(d15["atr"].iloc[-1])

        # FASE 1: H1
        t_h1=fase1_estrutura_h1(par)

        # FASE 2: Liquidez M15
        liq_bull,liq_bear,nivel_liq_low,nivel_liq_high=fase2_liquidez_m15(d15)

        # FASE 3: BOS/CHoCH M15
        bos_ok,choch_ok,tendencia_m15,tipo_bos=fase3_bos_choch_m15(d15)

        # FASE 4: Volume
        volume,vol_forte=fase4_volume(d15,par)

        # Define direcao
        if liq_bull and (tendencia_m15=="BULLISH" or bos_ok): dr="BUY"
        elif liq_bear and (tendencia_m15=="BEARISH" or bos_ok): dr="SELL"
        elif tendencia_m15=="BULLISH" and bos_ok: dr="BUY"
        elif tendencia_m15=="BEARISH" and bos_ok: dr="SELL"
        else:
            del d15; gc.collect()
            return {"par":nome,"score":0,"classificacao":"FRACO","emoji":"⚫","sinal":False,"dir":"","preco":preco_m15,"preco_m5":0,"sl":0,"tp1":0,"tp2":0,"rr":"","pips":0,"unidade":"pips","rsi":rsi,"tend_h1":t_h1,"tend_m15":tendencia_m15,"volume":volume,"atr":round(atr,5),"liq_ok":False,"bos_ok":bos_ok,"reteste_ok":False,"tipo_bos":tipo_bos,"tipo_reteste":"","ordem":"","nivel_liq":0,"sessao":s["sessao"],"operar":s["operar"],"raz":["Sem direcao clara"]}

        liq_ok=(liq_bull and dr=="BUY") or (liq_bear and dr=="SELL")
        nivel_liq=nivel_liq_low if dr=="BUY" else nivel_liq_high

        # FASE 5: Reteste M5
        reteste_ok,tipo_reteste,preco_m5,high_m5,low_m5=fase5_reteste_m5(par,dr,nivel_liq)
        preco_entrada=preco_m5 if reteste_ok and preco_m5>0 else preco_m15

        # PONTUACAO
        score=0; raz=[]

        # 3 OBRIGATORIOS (max 50 pts)
        if liq_ok: score+=20; raz.append("Liquidez capturada +20")
        if bos_ok:
            pts=15; label="BOS" if not choch_ok else "CHoCH"
            score+=pts; raz.append(f"{label} M15 +{pts}")
        if reteste_ok:
            score+=15; raz.append(f"Reteste M5 ({tipo_reteste}) +15")

        # FILTROS DE QUALIDADE (max 50 pts)
        if t_h1==dr: score+=20; raz.append(f"H1 alinhado {t_h1} +20")
        elif t_h1=="NEUTRO": score+=5; raz.append("H1 neutro +5")
        else: raz.append(f"H1 contra {t_h1}")

        if volume=="FORTE": score+=15; raz.append("Volume forte +15")
        elif volume=="MEDIO": score+=8; raz.append("Volume medio +8")
        else: raz.append("Volume fraco")

        if sessao_ok: score+=s["score_bonus"]; raz.append(f"Sessao {s['sessao']}")
        if choch_ok: score+=5; raz.append("CHoCH reversao +5")

        classificacao,emoji=classificar(score)

        # Tipo de ordem automatico
        ordem=tipo_ordem_auto(reteste_ok,tipo_reteste,vol_forte,liq_ok,dr)

        # Risco baseado no M5
        r=calc_risco(preco_entrada,dr,high_m5,low_m5,atr,par)

        # Sinal requer 3 obrigatorios + score >= 60
        sinal=liq_ok and bos_ok and reteste_ok and score>=60 and r["pips"]>0

        result={
            "par":nome,"score":score,"classificacao":classificacao,"emoji":emoji,
            "sinal":sinal,"dir":dr,"preco":preco_entrada,"preco_m15":preco_m15,"preco_m5":preco_m5,
            "sl":r["sl"],"tp1":r["tp1"],"tp2":r["tp2"],"rr":r["rr"],"pips":r["pips"],"unidade":r["unidade"],
            "rsi":rsi,"tend_h1":t_h1,"tend_m15":tendencia_m15,"volume":volume,"vol_forte":vol_forte,
            "atr":round(atr,5),"liq_ok":liq_ok,"bos_ok":bos_ok,"reteste_ok":reteste_ok,
            "tipo_bos":tipo_bos,"tipo_reteste":tipo_reteste,"ordem":ordem,
            "nivel_liq":nivel_liq,"sessao":s["sessao"],"operar":s["operar"],"raz":raz
        }
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
            "Par:          "+sinal["par"]+chr(10)+
            "Direccao:     "+sinal["dir"]+chr(10)+
            "Tipo Ordem:   "+sinal["ordem"]+chr(10)+
            "Entrada M5:   "+str(sinal["preco"])+chr(10)+
            "Ref M15:      "+str(sinal["preco_m15"])+chr(10)+
            "Score:        "+str(sinal["score"])+"%"+chr(10)+
            "RSI:          "+str(sinal["rsi"])+chr(10)+
            "Volume:       "+sinal["volume"]+chr(10)+
            "H1:           "+sinal["tend_h1"]+chr(10)+
            "M15:          "+sinal["tend_m15"]+chr(10)+
            "Reteste M5:   "+sinal["tipo_reteste"]+chr(10)+chr(10)+
            "3 OBRIGATORIOS"+chr(10)+
            "================================"+chr(10)+
            ("OK  " if sinal["liq_ok"] else "NOK ")+"Liquidez capturada"+chr(10)+
            ("OK  " if sinal["bos_ok"] else "NOK ")+"BOS/CHoCH M15 ("+sinal["tipo_bos"]+")"+chr(10)+
            ("OK  " if sinal["reteste_ok"] else "NOK ")+"Reteste M5 ("+sinal["tipo_reteste"]+")"+chr(10)+chr(10)+
            "GESTAO DE RISCO (baseado no M5)"+chr(10)+
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
        resend.Emails.send({
            "from":"onboarding@resend.dev",
            "to":email_destino,
            "subject":sinal["emoji"]+" "+sinal["par"]+" "+sinal["dir"]+" | "+sinal["ordem"]+" @ "+str(sinal["preco"])+" | "+str(sinal["score"])+"% "+str(sinal["classificacao"]),
            "text":corpo
        })
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

def grafico(par, dr, nivel_liq, preco_entrada):
    try:
        d=obter_dados(par,"15m","2d")
        if len(d)<10: return None
        d=adicionar_indicadores(d)
        df=d.tail(60)
        fig=go.Figure(go.Candlestick(
            x=df.index,open=df["Open"],high=df["High"],
            low=df["Low"],close=df["Close"],name=par,
            increasing_line_color="#26a69a",decreasing_line_color="#ef5350"
        ))
        if "ema20" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
        if "ema50" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
        if "ema200" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema200"],name="EMA200",line=dict(color="red",width=1)))
        if nivel_liq>0: fig.add_hline(y=nivel_liq,line_dash="dot",line_color="yellow",annotation_text="Zona Liquidez")
        if preco_entrada>0: fig.add_hline(y=preco_entrada,line_dash="dash",line_color="white",annotation_text="Entrada M5")
        fig.update_layout(title=f"{par} M15",xaxis_rangeslider_visible=False,height=420,template="plotly_dark",showlegend=False)
        del d; gc.collect()
        return fig
    except: return None

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started=True
    th=threading.Thread(target=monitor_background,daemon=True)
    th.start()

st.title("SMC Scanner Pro")
st.caption("EURUSD | GBPUSD | USDJPY | XAUUSD | H1+M15+M5 | Liquidez+BOS+Reteste | Monitor 24/7")
s=sessao_activa()
c1,c2,c3,c4=st.columns(4)
c1.metric("Sessao",s["sessao"])
c2.metric("Hora UTC",datetime.now(timezone.utc).strftime("%H:%M"))
c3.metric("Operar","SIM" if s["operar"] else "NAO")
c4.metric("Ativos","4 — EURUSD GBPUSD USDJPY XAUUSD")
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
            with st.expander(
                s["emoji"]+" "+s["par"]+" "+s["dir"]+" | "+s["ordem"]+" @ "+str(s["preco"])+" | "+str(s["score"])+"% "+str(s["classificacao"]),
                expanded=True
            ):
                st.markdown("### "+s["emoji"]+" "+s["par"]+" — "+s["dir"]+" — "+str(s["classificacao"]))
                c1,c2,c3,c4=st.columns(4)
                c1.metric("Tipo Ordem",s["ordem"])
                c2.metric("Entrada M5",s["preco"])
                c3.metric("Score",str(s["score"])+"%")
                c4.metric("Classificacao",str(s["classificacao"]))
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
                st.markdown("**5 Fases do Sistema:**")
                col1,col2,col3=st.columns(3)
                col1.metric("Fase 2 — Liquidez","OK" if s["liq_ok"] else "NOK")
                col2.metric("Fase 3 — "+s["tipo_bos"],"OK" if s["bos_ok"] else "NOK")
                col3.metric("Fase 5 — Reteste M5",s["tipo_reteste"] if s["reteste_ok"] else "NOK")
                st.caption("R:R "+s["rr"]+" | ATR: "+str(s["atr"])+" | M15 ref: "+str(s.get("preco_m15","")))
                st.caption(" | ".join(s["raz"]))
                par_original=[p for p in pares if nomes.get(p,"")==s["par"]]
                par_key=par_original[0] if par_original else s["par"]
                fig=grafico(par_key,s["dir"],s.get("nivel_liq",0),s["preco"])
                if fig: st.plotly_chart(fig,use_container_width=True)
    else:
        st.info("Sem sinais. Sistema aguarda: Liquidez + BOS/CHoCH + Reteste M5.")

    if resultados:
        st.subheader("Estado do mercado — 4 ativos")
        linhas=[]
        for r in resultados:
            obrig=sum([r.get("liq_ok",False),r.get("bos_ok",False),r.get("reteste_ok",False)])
            if r.get("sinal"): status=r.get("emoji","")+" "+str(r.get("classificacao",""))
            elif obrig==2: status="QUASE (2/3)"
            elif obrig==1: status="AGUARDA (1/3)"
            else: status="FRACO"
            linhas.append({
                "Status":status,
                "Par":r["par"],
                "Dir":r.get("dir",""),
                "Score":str(r.get("score",0))+"%",
                "RSI":r.get("rsi",0),
                "H1":r.get("tend_h1",""),
                "M15":r.get("tend_m15",""),
                "Volume":r.get("volume",""),
                "Liq":"OK" if r.get("liq_ok") else "NOK",
                "BOS":r.get("tipo_bos",""),
                "Reteste M5":r.get("tipo_reteste",""),
                "Entrada":r.get("preco",0),
                "SL":r.get("sl",0),
                "TP1":r.get("tp1",0),
                "TP2":r.get("tp2",0)
            })
        st.dataframe(pd.DataFrame(linhas),use_container_width=True)

st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | PREMIUM>=85% NORMAL>=70% ACEITAVEL>=60% | 3 obrigatorios: Liquidez+BOS+Reteste M5")
