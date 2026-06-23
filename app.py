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
import requests
from datetime import datetime, timezone
import sys
sys.path.insert(0, "/app")
from scanner_smc import escanear_par, pares_smc, nomes_smc, obter_contexto_macro

try:
    import resend
    RESEND_OK = True
except:
    RESEND_OK = False

st.set_page_config(page_title="Trading Pro", page_icon="📈", layout="wide")

# ==================== XAUUSD SIGNAL — FUNCOES ====================
def obter_dados(intervalo="15m", periodo="7d"):
    try:
        d = yf.download("GC=F", period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except: return pd.DataFrame()

def adicionar_indicadores(df):
    if len(df)<30: return df
    df = df.copy()
    df["ema20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["Close"], window=50).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"],df["Low"],df["Close"],window=14).average_true_range()
    df["adx"] = ta.trend.ADXIndicator(df["High"],df["Low"],df["Close"],window=14).adx()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute/60
    if 13<=h<=17: return {"sessao":"SOBREPOSICAO","operar":True}
    elif 8<=h<=17: return {"sessao":"LONDRES","operar":True}
    elif 13<=h<=22: return {"sessao":"NOVA YORK","operar":True}
    else: return {"sessao":"ASIATICA","operar":False}

def verificar_noticias():
    try:
        agora = datetime.now(timezone.utc)
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=5)
        if r.status_code != 200: return False, ""
        eventos = r.json()
        for ev in eventos:
            if ev.get("impact","") != "High": continue
            if not any(m in ev.get("title","").upper() for m in ["NFP","CPI","FOMC","FEDERAL","INTEREST","GDP"]): continue
            try:
                ev_time = datetime.strptime(ev["date"], "%Y-%m-%dT%H:%M:%S%z")
                diff = abs((agora - ev_time).total_seconds() / 60)
                if diff <= 30: return True, ev.get("title","Noticia alto impacto")
            except: continue
        return False, ""
    except: return False, ""

def analise_h1():
    try:
        d = obter_dados("1h","10d")
        if len(d)<30: return None
        d = adicionar_indicadores(d)
        if len(d)<5: return None
        p = float(d["Close"].iloc[-1]); e20 = float(d["ema20"].iloc[-1]); e50 = float(d["ema50"].iloc[-1])
        adx = float(d["adx"].iloc[-1])
        if adx <= 25: return {"trend":"NO_SIGNAL","adx":round(adx,1),"motivo":"ADX <= 25 mercado lateral"}
        if e20 > e50 and p > e20: return {"trend":"BUY","adx":round(adx,1),"e20":round(e20,2),"e50":round(e50,2),"preco":round(p,2)}
        elif e20 < e50 and p < e20: return {"trend":"SELL","adx":round(adx,1),"e20":round(e20,2),"e50":round(e50,2),"preco":round(p,2)}
        else: return {"trend":"NO_SIGNAL","adx":round(adx,1),"motivo":"EMAs desalinhadas"}
    except: return None

def analise_m15(trend):
    try:
        d = obter_dados("15m","5d")
        if len(d)<30: return None
        d = adicionar_indicadores(d)
        if len(d)<5: return None
        p = float(d["Close"].iloc[-1]); o = float(d["Open"].iloc[-1])
        e20 = float(d["ema20"].iloc[-1]); rsi = float(d["rsi"].iloc[-1])
        vela_bull = p > o; vela_bear = p < o
        if trend == "BUY":
            conf = rsi > 50 and vela_bull and p > e20
            return {"conf":conf,"rsi":round(rsi,1),"vela":"BULL" if vela_bull else "BEAR","ema20":round(e20,2),"preco":round(p,2),"atr":float(d["atr"].iloc[-1])}
        else:
            conf = rsi < 50 and vela_bear and p < e20
            return {"conf":conf,"rsi":round(rsi,1),"vela":"BULL" if vela_bull else "BEAR","ema20":round(e20,2),"preco":round(p,2),"atr":float(d["atr"].iloc[-1])}
    except: return None

def detectar_vela_rejeicao(df, trend):
    u = df.iloc[-1]; a = df.iloc[-2]
    corpo_u = abs(float(u["Close"])-float(u["Open"]))
    amplitude_u = float(u["High"])-float(u["Low"])
    if amplitude_u == 0: return False, "SEM_VELA"
    if trend == "BUY":
        sombra_inf = float(u["Open"])-float(u["Low"]) if float(u["Close"])>float(u["Open"]) else float(u["Close"])-float(u["Low"])
        hammer = sombra_inf >= corpo_u * 2 and float(u["Close"]) > float(u["Open"])
        pin_bar = sombra_inf > amplitude_u * 0.6 and float(u["Close"]) > float(u["Open"])
        engulfing = (float(u["Close"]) > float(a["High"])) and (float(u["Open"]) < float(a["Low"])) and float(u["Close"]) > float(u["Open"])
        if hammer: return True, "HAMMER"
        elif pin_bar: return True, "PIN_BAR_BULL"
        elif engulfing: return True, "ENGULFING_BULL"
        else: return False, "SEM_VELA"
    else:
        sombra_sup = float(u["High"])-float(u["Open"]) if float(u["Close"])<float(u["Open"]) else float(u["High"])-float(u["Close"])
        shooting = sombra_sup >= corpo_u * 2 and float(u["Close"]) < float(u["Open"])
        pin_bar = sombra_sup > amplitude_u * 0.6 and float(u["Close"]) < float(u["Open"])
        engulfing = (float(u["Close"]) < float(a["Low"])) and (float(u["Open"]) > float(a["High"])) and float(u["Close"]) < float(u["Open"])
        if shooting: return True, "SHOOTING_STAR"
        elif pin_bar: return True, "PIN_BAR_BEAR"
        elif engulfing: return True, "ENGULFING_BEAR"
        else: return False, "SEM_VELA"

def analise_m5(trend):
    try:
        d = obter_dados("5m","2d")
        if len(d)<20: return None
        d = adicionar_indicadores(d)
        if len(d)<10: return None
        p = float(d["Close"].iloc[-1]); e20 = float(d["ema20"].iloc[-1]); atr = float(d["atr"].iloc[-1])
        vol = float(d["Volume"].iloc[-1]); vol_med = float(d["vol_medio"].iloc[-1]) if float(d["vol_medio"].iloc[-1])>0 else 1
        dist_ema = abs(p - e20) / atr
        retracao = dist_ema < 1.5
        vela_ok, tipo_vela = detectar_vela_rejeicao(d, trend)
        u = d.iloc[-1]; prev = d.iloc[-2]
        if trend == "BUY": rompimento = float(u["Close"]) > float(prev["High"])
        else: rompimento = float(u["Close"]) < float(prev["Low"])
        vol_ok = vol > vol_med
        d2=d.copy()
        d2["sh"]=((d2["High"]>d2["High"].shift(1))&(d2["High"]>d2["High"].shift(-1))&(d2["High"]>d2["High"].shift(2))&(d2["High"]>d2["High"].shift(-2)))
        d2["sl"]=((d2["Low"]<d2["Low"].shift(1))&(d2["Low"]<d2["Low"].shift(-1))&(d2["Low"]<d2["Low"].shift(2))&(d2["Low"]<d2["Low"].shift(-2)))
        sh=d2[d2["sh"]]["High"]; sl=d2[d2["sl"]]["Low"]
        if trend == "BUY":
            sl_price = round(float(sl.iloc[-1]) - atr*0.5, 2) if len(sl)>0 else round(p - atr*2, 2)
        else:
            sl_price = round(float(sh.iloc[-1]) + atr*0.5, 2) if len(sh)>0 else round(p + atr*2, 2)
        risco = abs(p - sl_price)
        tp = round(p + risco*2, 2) if trend == "BUY" else round(p - risco*2, 2)
        del d,d2; gc.collect()
        return {"preco": round(p,2),"e20_m5": round(e20,2),"retracao": retracao,"vela_ok": vela_ok,"tipo_vela": tipo_vela,"rompimento": rompimento,"vol_ok": vol_ok,"sl": sl_price,"tp": tp,"rr": "1:2","atr_m5": round(atr,2)}
    except:
        gc.collect()
        return None

def filtro_sr(trend, preco, atr):
    try:
        d = obter_dados("1h","10d")
        d = adicionar_indicadores(d)
        highs = d["High"].tail(50); lows = d["Low"].tail(50)
        resistencia = float(highs.max()); suporte = float(lows.min())
        if trend == "BUY":
            dist = resistencia - preco
            return dist > atr, round(resistencia,2)
        else:
            dist = preco - suporte
            return dist > atr, round(suporte,2)
    except: return True, 0

def calcular_score(h1, m15, m5, sr_ok, noticias, sessao_ok):
    score = 0; detalhes = {}
    if h1 and h1["trend"] != "NO_SIGNAL":
        score += 20; detalhes["Tendencia H1"] = "+20"
        if h1["adx"] > 25: score += 15; detalhes["ADX > 25"] = "+15"
    if m15 and m15["conf"]: score += 15; detalhes["Confirmacao M15"] = "+15"
    if m5:
        if m5["retracao"]: score += 10; detalhes["Retracao M5"] = "+10"
        if m5["vela_ok"]: score += 15; detalhes["Vela rejeicao ("+m5["tipo_vela"]+")"] = "+15"
        if m5["rompimento"]: score += 10; detalhes["Rompimento confirmado"] = "+10"
        if m5["vol_ok"]: score += 5; detalhes["Volume acima media"] = "+5"
    if sr_ok: score += 5; detalhes["Distancia S/R segura"] = "+5"
    if not noticias: score += 5; detalhes["Sem noticias alto impacto"] = "+5"
    return score, detalhes

def classificar_confianca(score):
    if score >= 90: return "MUITO FORTE","🔥"
    elif score >= 80: return "FORTE","🟢"
    elif score >= 70: return "MODERADO","🟡"
    else: return "NAO ENVIAR","⚫"

def analisar_xauusd(ignorar_sessao=False):
    try:
        s = sessao_activa()
        sessao_ok = s["operar"] if not ignorar_sessao else True
        tem_noticias, noticia_nome = verificar_noticias()
        if tem_noticias:
            return {"sinal":False,"motivo":f"BLOQUEADO: {noticia_nome}","sessao":s["sessao"]}
        h1 = analise_h1()
        if not h1 or h1["trend"] == "NO_SIGNAL":
            motivo = h1["motivo"] if h1 else "Erro H1"
            return {"sinal":False,"motivo":motivo,"sessao":s["sessao"],"h1":h1}
        trend = h1["trend"]
        m15 = analise_m15(trend)
        if not m15 or not m15["conf"]:
            return {"sinal":False,"motivo":"M15 nao confirma H1","sessao":s["sessao"],"h1":h1,"m15":m15}
        m5 = analise_m5(trend)
        if not m5:
            return {"sinal":False,"motivo":"Erro M5","sessao":s["sessao"],"h1":h1,"m15":m15}
        sr_ok, nivel_sr = filtro_sr(trend, m5["preco"], m5["atr_m5"])
        score, detalhes = calcular_score(h1, m15, m5, sr_ok, tem_noticias, sessao_ok)
        confianca, emoji = classificar_confianca(score)
        obrigatorios = h1["trend"]!="NO_SIGNAL" and m15["conf"] and m5["retracao"] and m5["vela_ok"] and m5["rompimento"] and m5["vol_ok"] and sr_ok
        sinal = score >= 70 and sessao_ok and obrigatorios
        return {"sinal": sinal,"trend": trend,"emoji": emoji,"confianca": confianca,"score": score,"detalhes": detalhes,"preco": m5["preco"],"sl": m5["sl"],"tp": m5["tp"],"rr": m5["rr"],"tipo_vela": m5["tipo_vela"],"h1": h1,"m15": m15,"m5": m5,"sr_ok": sr_ok,"nivel_sr": nivel_sr,"sessao": s["sessao"],"operar": sessao_ok,"noticias": tem_noticias}
    except:
        gc.collect()
        return {"sinal":False,"motivo":"Erro interno","sessao":""}

def enviar_email(r):
    try:
        if not RESEND_OK: return
        resend.api_key = os.environ.get("RESEND_API_KEY","")
        email_destino = os.environ.get("EMAIL_DESTINO","")
        if not resend.api_key or not email_destino: return
        det_txt = chr(10).join([k+" "+v for k,v in r["detalhes"].items()])
        corpo = (r["emoji"]+" XAUUSD "+r["trend"]+" — "+r["confianca"]+chr(10)+"================================"+chr(10)+"Par:        XAUUSD"+chr(10)+"Direccao:   "+r["trend"]+chr(10)+"Entrada:    "+str(r["preco"])+chr(10)+"Stop Loss:  "+str(r["sl"])+chr(10)+"Take Profit:"+str(r["tp"])+chr(10)+"Risk Reward:"+r["rr"]+chr(10)+"Confianca:  "+str(r["score"])+"%"+chr(10)+chr(10)+"TIMEFRAMES"+chr(10)+"================================"+chr(10)+"Tendencia H1:    "+r["h1"]["trend"]+" (ADX="+str(r["h1"]["adx"])+")"+chr(10)+"Confirmacao M15: "+("OK" if r["m15"]["conf"] else "NOK")+" RSI="+str(r["m15"]["rsi"])+chr(10)+"Entrada M5:      Confirmada ("+r["tipo_vela"]+")"+chr(10)+chr(10)+"PONTUACAO"+chr(10)+"================================"+chr(10)+det_txt)
        resend.Emails.send({"from":"onboarding@resend.dev","to":email_destino,"subject":r["emoji"]+" XAUUSD "+r["trend"]+" @ "+str(r["preco"])+" | "+str(r["score"])+"% "+r["confianca"],"text":corpo})
    except: pass

sinais_enviados = set()

def monitor_background():
    global sinais_enviados
    while True:
        try:
            r = analisar_xauusd()
            if r and r.get("sinal"):
                chave = f"XAUUSD_{r['trend']}_{r['score']}"
                if chave not in sinais_enviados:
                    enviar_email(r)
                    sinais_enviados.add(chave)
                    if len(sinais_enviados)>10: sinais_enviados.clear()
            time.sleep(2)
        except: pass
        time.sleep(300)
        gc.collect()

def grafico_xauusd(tf="15m"):
    try:
        periodo = "2d" if tf in ["5m","15m"] else "10d"
        d = obter_dados(tf, periodo)
        if len(d)<10: return None
        d = adicionar_indicadores(d)
        df = d.tail(80)
        fig = go.Figure(go.Candlestick(x=df.index, open=df["Open"], high=df["High"],low=df["Low"], close=df["Close"], name="XAUUSD",increasing_line_color="#26a69a", decreasing_line_color="#ef5350"))
        if "ema20" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema20"],name="EMA20",line=dict(color="orange",width=1)))
        if "ema50" in df.columns: fig.add_trace(go.Scatter(x=df.index,y=df["ema50"],name="EMA50",line=dict(color="blue",width=1)))
        fig.update_layout(title=f"XAUUSD {tf.upper()}",xaxis_rangeslider_visible=False,height=400,template="plotly_dark",showlegend=False)
        del d; gc.collect()
        return fig
    except: return None

if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = True
    th = threading.Thread(target=monitor_background, daemon=True)
    th.start()

# ==================== MENU LATERAL ====================
st.sidebar.title("📊 Trading Pro")
pagina = st.sidebar.radio("Escolhe a ferramenta:", ["🥇 Sinal XAUUSD", "🔍 Scanner SMC (4 pares)"])
st.sidebar.divider()
st.sidebar.caption("Sinal XAUUSD: gera sinais de entrada com confianca")
st.sidebar.caption("Scanner SMC: marca zonas institucionais para desenhares no MT5")

# ==================== PAGINA 1: SINAL XAUUSD ====================
if pagina == "🥇 Sinal XAUUSD":
    st.title("🥇 XAUUSD Signal Pro")
    st.caption("H1 Tendencia | M15 Confirmacao | M5 Entrada | ADX + Velas + Volume | Monitor 24/7")
    s = sessao_activa()
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Sessao", s["sessao"])
    c2.metric("Hora UTC", datetime.now(timezone.utc).strftime("%H:%M"))
    c3.metric("Operar", "SIM" if s["operar"] else "NAO")
    c4.metric("Par", "XAUUSD (Ouro)")
    st.divider()

    if st.button("Analisar XAUUSD", type="primary"):
        with st.spinner("A analisar H1 + M15 + M5..."):
            r = analisar_xauusd(ignorar_sessao=True)
            gc.collect()
        if r.get("sinal"):
            st.success(r["emoji"]+" SINAL CONFIRMADO — "+r["confianca"]+" ("+str(r["score"])+"%)")
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Direccao", r["trend"]); c2.metric("Entrada", r["preco"]); c3.metric("Confianca", str(r["score"])+"%"); c4.metric("Classificacao", r["confianca"])
            c1b,c2b,c3b = st.columns(3)
            c1b.metric("Stop Loss", r["sl"]); c2b.metric("Take Profit", r["tp"]); c3b.metric("Risk Reward", r["rr"])
            st.markdown("**Timeframes:**")
            col1,col2,col3 = st.columns(3)
            col1.metric("H1 Tendencia", r["h1"]["trend"]+" ADX="+str(r["h1"]["adx"]))
            col2.metric("M15 Confirmacao", "OK RSI="+str(r["m15"]["rsi"]))
            col3.metric("M5 Vela", r["tipo_vela"])
            st.markdown("**Pontuacao detalhada:**")
            for k,v in r["detalhes"].items(): st.write("✅ "+k+" "+v)
            tab1,tab2,tab3 = st.tabs(["H1","M15","M5"])
            with tab1:
                fig=grafico_xauusd("1h")
                if fig: st.plotly_chart(fig,use_container_width=True)
            with tab2:
                fig=grafico_xauusd("15m")
                if fig: st.plotly_chart(fig,use_container_width=True)
            with tab3:
                fig=grafico_xauusd("5m")
                if fig: st.plotly_chart(fig,use_container_width=True)
        else:
            motivo = r.get("motivo","Condicoes nao cumpridas")
            st.info("Sem sinal agora. Motivo: "+motivo)
            st.subheader("Estado actual")
            if r.get("h1"):
                h1 = r["h1"]
                st.write("**H1:** "+h1.get("trend","")+" | ADX: "+str(h1.get("adx","")))
            if r.get("m15"):
                m15 = r["m15"]
                st.write("**M15:** "+("Confirma" if m15.get("conf") else "Nao confirma")+" | RSI: "+str(m15.get("rsi","")))
            if r.get("m5"):
                m5 = r["m5"]
                st.write("**M5:** Preco="+str(m5.get("preco",""))+" | Retracao="+("SIM" if m5.get("retracao") else "NAO")+" | Vela="+m5.get("tipo_vela",""))
            st.subheader("Graficos")
            tab1,tab2,tab3 = st.tabs(["H1","M15","M5"])
            with tab1:
                fig=grafico_xauusd("1h")
                if fig: st.plotly_chart(fig,use_container_width=True)
            with tab2:
                fig=grafico_xauusd("15m")
                if fig: st.plotly_chart(fig,use_container_width=True)
            with tab3:
                fig=grafico_xauusd("5m")
                if fig: st.plotly_chart(fig,use_container_width=True)
    st.caption("Actualizado: "+datetime.now().strftime("%H:%M:%S")+" | Sinal >= 70% | MUITO FORTE>=90 FORTE>=80 MODERADO>=70")

# ==================== PAGINA 2: SCANNER SMC ====================
else:
    st.title("Scanner SMC — Mapa Institucional")
    st.caption("EURUSD | GBPUSD | USDJPY | XAUUSD | BTCUSD | Leitura pura de estrutura e liquidez, sem sugestao de trade")
    st.divider()

    # CONTEXTO GLOBAL
    with st.spinner("A carregar contexto macro..."):
        macro = obter_contexto_macro()

    st.markdown("### Contexto Global")
    c1,c2,c3 = st.columns(3)
    c1.metric("USD", macro["usd"])
    c2.metric("OURO", macro["ouro"])
    c3.metric("RISCO GLOBAL", macro["risco"])
    if macro["proximo_evento"]:
        st.info("Proximo evento de alto impacto: " + macro["proximo_evento"])
    st.divider()

    with st.expander("Como ler este scanner", expanded=False):
        st.markdown("""
**Fluxo Institucional (H1)** = direcao principal do mercado baseada na estrutura de topos e fundos.
**Estrutura M15** = classificacao da estrutura operacional: ALTA / BAIXA / TRANSICAO / CONSOLIDACAO.
**Estado M15** = o que a estrutura esta a fazer agora: EXPANSAO / REVERSAO / CORRECAO / INDEFINIDO.
**Estrutura M5** = confirmacao de precisao na mesma logica.

**Classificacao da estrutura:**
- **ALTA** = HH + HL (topos e fundos crescentes)
- **BAIXA** = LH + LL (topos e fundos decrescentes)
- **TRANSICAO** = HH + LL (expansao dos extremos, sem direcao clara)
- **CONSOLIDACAO** = LH + HL (compressao, mercado a acumular)

**Qualidade:**
- **A+** = Macro + H1 + M15 + M5 alinhados
- **A** = H1 + M15 + M5 alinhados
- **B** = H1 + M15 alinhados
- **C** = Apenas M15 + M5 alinhados
- **D** = Estruturas em conflito

**Liquidez:** BSL varrida quando High supera o nivel. SSL varrida quando Low fica abaixo do nivel.
        """)

    if st.button("Escanear mercado", type="primary"):
        resultados = []
        prog = st.progress(0)
        for i, par in enumerate(pares_smc):
            r = escanear_par(par, macro)
            if r: resultados.append(r)
            prog.progress((i+1)/len(pares_smc))
            gc.collect()

        for r in resultados:
            st.markdown("---")
            st.markdown("## " + r["par"])

            # RESUMO
            c1,c2,c3 = st.columns(3)
            ctx_label = "ALINHADO" if r["ctx_macro"]=="ALINHADO" else "NEUTRO" if r["ctx_macro"]=="NEUTRO" else "CONTRA"
            c1.metric("Contexto Macro", ctx_label)
            c2.metric("Qualidade da Estrutura", r["qualidade"])
            c3.metric("Fluxo Institucional (H1)", r["fluxo_h1"])

            c1b,c2b,c3b,c4b = st.columns(4)
            c1b.metric("Estrutura M15", r["est_m15"]["estrutura"])
            c2b.metric("Estado M15", r["est_m15"]["estado"])
            c3b.metric("Estrutura M5", r["est_m5"]["estrutura"])
            c4b.metric("Estado M5", r["est_m5"]["estado"])

            # FASE DE MERCADO
            st.info(r["fase"] + " — " + r["fase_desc"])

            # ESTRUTURA
            with st.expander("Estrutura (M15 + M5)", expanded=False):
                if r["est_m15"]["pontos"]:
                    st.markdown("**M15:**")
                    for e in r["est_m15"]["pontos"]:
                        hora_str = e["hora"].strftime("%H:%M") if hasattr(e["hora"],"strftime") else str(e["hora"])
                        st.write(e["tipo"] + " — Preco: " + str(e["preco"]) + " — Hora: " + hora_str)
                if r["est_m5"]["pontos"]:
                    st.markdown("**M5:**")
                    for e in r["est_m5"]["pontos"]:
                        hora_str = e["hora"].strftime("%H:%M") if hasattr(e["hora"],"strftime") else str(e["hora"])
                        st.write(e["tipo"] + " — Preco: " + str(e["preco"]) + " — Hora: " + hora_str)
                if not r["est_m15"]["pontos"] and not r["est_m5"]["pontos"]:
                    st.write("Sem estrutura relevante no momento")

            # LIQUIDEZ
            with st.expander("Liquidez", expanded=False):
                liq = r["liquidez"]
                st.write("BSL (liquidez acima): " + str(liq["bsl"]) + " — " + liq["bsl_estado"])
                st.write("SSL (liquidez abaixo): " + str(liq["ssl"]) + " — " + liq["ssl_estado"])
                if liq["eqh"]: st.write("EQH (topos iguais): " + str(liq["eqh"]) + " — " + str(liq["eqh_estado"]))
                if liq["eql"]: st.write("EQL (fundos iguais): " + str(liq["eql"]) + " — " + str(liq["eql_estado"]))
                if liq["grab_topo"]: st.write("Varredura de liquidez detectada no topo")
                if liq["grab_fundo"]: st.write("Varredura de liquidez detectada no fundo")

            # ORDER BLOCKS
            with st.expander("Blocos de Ordem (forca >= 70%)", expanded=True):
                obs = r["ob_h1"] + r["ob_m15"]
                if obs:
                    for ob in obs:
                        hora_c = ob["hora_criacao"].strftime("%H:%M") if hasattr(ob["hora_criacao"],"strftime") else str(ob["hora_criacao"])
                        st.markdown("**" + ob["tipo"] + " (" + ob["tf"] + ")**")
                        col1,col2 = st.columns(2)
                        col1.write("Alto: " + str(ob["alto"]))
                        col2.write("Baixo: " + str(ob["baixo"]))
                        col1.write("Criado: " + hora_c)
                        col2.write("Idade: " + ob["idade"])
                        col1.write("Testes: " + str(ob["testes"]))
                        col2.write("Estado: VALIDO | Forca: " + str(ob["forca"]) + "%")
                        st.divider()
                else:
                    st.write("Nenhum bloco de ordem valido no momento")

            # FVG
            with st.expander("Gap de Valor Justo (H1 + M15 + M5)", expanded=True):
                fvgs = r["fvg_h1"] + r["fvg_m15"] + r["fvg_m5"]
                fvgs_ativos = [f for f in fvgs if f["estado"] != "PREENCHIDO"]
                if fvgs_ativos:
                    for f in fvgs_ativos:
                        st.markdown("**" + f["tipo"] + " (" + f["tf"] + ")**")
                        col1,col2 = st.columns(2)
                        col1.write("Topo: " + str(f["topo"]))
                        col2.write("Base: " + str(f["base"]))
                        col1.write("Estado: " + f["estado"])
                        col2.write("Idade: " + f["idade"])
                        st.divider()
                else:
                    st.write("Nenhum gap activo no momento")

            # NIVEIS INSTITUCIONAIS
            with st.expander("Niveis Institucionais", expanded=False):
                niv = r["niveis"]
                col1,col2 = st.columns(2)
                col1.write("PDH (Maxima dia anterior): " + str(niv["pdh"]))
                col1.write("PDL (Minima dia anterior): " + str(niv["pdl"]))
                col2.write("PWH (Maxima semana anterior): " + str(niv["pwh"]))
                col2.write("PWL (Minima semana anterior): " + str(niv["pwl"]))

    else:
        st.info("Clica em 'Escanear mercado' para mapear a estrutura institucional dos 5 pares.")

    st.caption("Actualizado: " + datetime.now().strftime("%H:%M:%S") + " | Apenas leitura SMC — sem sugestao de entrada")
