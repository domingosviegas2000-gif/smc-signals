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

# ==================== CONFIGURAÇÃO ====================
MODO = "NORMAL"  # Opções: "AGRESSIVO" (mais sinais) ou "CONSERVADOR" (menos sinais, maior qualidade)

st.set_page_config(page_title="SMC Scanner Pro", page_icon="📈", layout="wide")

pares = ["EURUSD=X","GBPUSD=X","USDJPY=X","GC=F"]
nomes = {"EURUSD=X":"EURUSD","GBPUSD=X":"GBPUSD","USDJPY=X":"USDJPY","GC=F":"XAUUSD"}

# ==================== FUNÇÕES BASE ====================
def obter_dados(par, intervalo="15m", periodo="7d"):
    try:
        d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except:
        return pd.DataFrame()

def adicionar_indicadores(df):
    if len(df) < 20:
        return df
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["ema20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["Close"], window=50).ema_indicator()
    df["ema100"] = ta.trend.EMAIndicator(df["Close"], window=100).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["High"], df["Low"], df["Close"], window=14).average_true_range()
    df["corpo"] = abs(df["Close"] - df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    df["vol_medio"] = df["Volume"].rolling(20).mean()
    return df.dropna()

def sessao_activa():
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60
    if 13 <= h <= 17:
        return {"sessao": "SOBREPOSICAO", "score_bonus": 20, "operar": True}
    elif 8 <= h <= 17:
        return {"sessao": "LONDRES", "score_bonus": 15, "operar": True}
    elif 13 <= h <= 22:
        return {"sessao": "NOVA YORK", "score_bonus": 15, "operar": True}
    else:
        return {"sessao": "ASIATICA", "score_bonus": 0, "operar": False}

def classificar(score):
    if score >= 85:
        return "PREMIUM", "🟢"
    elif score >= 70:
        return "NORMAL", "🟡"
    elif score >= 60:
        return "ACEITAVEL", "🔵"
    else:
        return "FRACO", "⚫"

# ==================== FASE 1: H1 ====================
def fase1_h1(par):
    try:
        d = obter_dados(par, "1h", "10d")
        if len(d) < 10:
            return "BULLISH"
        d = adicionar_indicadores(d)
        if len(d) < 3:
            return "BULLISH"
        p = float(d["Close"].iloc[-1])
        e20 = float(d["ema20"].iloc[-1])
        del d
        gc.collect()
        return "BULLISH" if p > e20 else "BEARISH"
    except:
        return "BULLISH"

# ==================== FASE 2: LIQUIDEZ M15 (CORRIGIDA) ====================
def fase2_liquidez(df, dr):
    try:
        df2 = df.copy()
        df2["sh"] = ((df2["High"] > df2["High"].shift(1)) & 
                     (df2["High"] > df2["High"].shift(-1)) &
                     (df2["High"] > df2["High"].shift(2)) & 
                     (df2["High"] > df2["High"].shift(-2)))
        df2["sl"] = ((df2["Low"] < df2["Low"].shift(1)) & 
                     (df2["Low"] < df2["Low"].shift(-1)) &
                     (df2["Low"] < df2["Low"].shift(2)) & 
                     (df2["Low"] < df2["Low"].shift(-2)))
        
        sh = df2[df2["sh"]]["High"]
        sl = df2[df2["sl"]]["Low"]
        
        if len(sh) < 2 or len(sl) < 2:
            return "NAO OCORREU", False, 0, 0
        
        ush1 = float(sh.iloc[-1])
        ush2 = float(sh.iloc[-2])
        usl1 = float(sl.iloc[-1])
        usl2 = float(sl.iloc[-2])
        
        p = float(df["Close"].iloc[-1])
        
        if dr == "BUY":
            nivel_liq = min(usl1, usl2)
            low_15 = df["Low"].tail(15).min()
            liq_ok = (low_15 <= nivel_liq) and (p > nivel_liq)
        else:
            nivel_liq = max(ush1, ush2)
            high_15 = df["High"].tail(15).max()
            liq_ok = (high_15 >= nivel_liq) and (p < nivel_liq)
        
        if liq_ok:
            estado = "VARRIDA"
        elif abs(p - nivel_liq) / nivel_liq < 0.003:
            estado = "PROXIMA"
        else:
            estado = "NAO OCORREU"
        
        return estado, liq_ok, nivel_liq, 0
    except Exception as e:
        return "NAO OCORREU", False, 0, 0

# ==================== FASE 3: BOS/CHoCH M15 (CORRIGIDA - COM FILTRO TEMPORAL) ====================
def fase3_bos(df, dr):
    try:
        df2 = df.copy()
        df2["sh"] = ((df2["High"] > df2["High"].shift(1)) & 
                     (df2["High"] > df2["High"].shift(-1)) &
                     (df2["High"] > df2["High"].shift(2)) &
                     (df2["High"] > df2["High"].shift(-2)))
        df2["sl"] = ((df2["Low"] < df2["Low"].shift(1)) & 
                     (df2["Low"] < df2["Low"].shift(-1)) &
                     (df2["Low"] < df2["Low"].shift(2)) &
                     (df2["Low"] < df2["Low"].shift(-2)))
        
        sh = df2[df2["sh"]]["High"]
        sl = df2[df2["sl"]]["Low"]
        
        if len(sh) < 2 or len(sl) < 2:
            return False, False, "AGUARDA"
        
        # FILTRO TEMPORAL: só swings das últimas 10 velas
        ultimas_velas = df.index[-10:]
        
        sh_filtrado = [sh[i] for i in sh.index if i in ultimas_velas]
        sl_filtrado = [sl[i] for i in sl.index if i in ultimas_velas]
        
        if len(sh_filtrado) < 1 or len(sl_filtrado) < 1:
            return False, False, "AGUARDA"
        
        ush = float(sh_filtrado[-1]) if sh_filtrado else 0
        usl = float(sl_filtrado[-1]) if sl_filtrado else 0
        
        p = float(df["Close"].iloc[-1])
        pp = float(df["Close"].iloc[-2])
        
        bos_bull = (p > ush) and (pp > ush) and ush > 0
        bos_bear = (p < usl) and (pp < usl) and usl > 0
        
        choch_bull = False
        choch_bear = False
        
        if len(sh_filtrado) >= 2 and len(sl_filtrado) >= 2:
            ush_ant = float(sh_filtrado[-2]) if len(sh_filtrado) >= 2 else ush
            usl_ant = float(sl_filtrado[-2]) if len(sl_filtrado) >= 2 else usl
            
            hh = ush > ush_ant
            hl = usl > usl_ant
            lh = ush < ush_ant
            ll = usl < usl_ant
            
            choch_bull = (lh and ll) and bos_bull
            choch_bear = (hh and hl) and bos_bear
        
        if dr == "BUY":
            bos_ok = bos_bull
            choch_ok = choch_bull
        else:
            bos_ok = bos_bear
            choch_ok = choch_bear
        
        if choch_ok:
            tipo = "CHoCH"
        elif bos_ok:
            tipo = "BOS"
        else:
            tipo = "AGUARDA"
        
        return bos_ok, choch_ok, tipo
    except Exception as e:
        return False, False, "AGUARDA"

# ==================== FASE 4: VOLUME (CORRIGIDA - LIMIARES AJUSTADOS) ====================
def fase4_volume(df, par):
    try:
        eh_ouro = "GC" in par
        u = df.iloc[-1]
        corpo = float(u["corpo"])
        media_corpo = float(u["media_corpo"])
        vol = float(u["Volume"])
        vol_med = float(u["vol_medio"]) if float(u["vol_medio"]) > 0 else 1
        
        if eh_ouro:
            if corpo > media_corpo * 1.5:
                return "FORTE", True
            elif corpo > media_corpo * 0.8:
                return "MEDIO", False
            else:
                return "FRACO", False
        else:
            # FOREX: limiares reduzidos porque tick volume é estável
            if vol > vol_med * 1.2 and corpo > media_corpo * 1.0:
                return "FORTE", True
            elif vol > vol_med * 0.85:
                return "MEDIO", False
            else:
                return "FRACO", False
    except Exception as e:
        return "MEDIO", False

# ==================== FASE 5: RETESTE M5 (CORRIGIDA - EXIGE BOS M15) ====================
def fase5_reteste_m5(par, dr, bos_ok_m15):
    try:
        d = obter_dados(par, "5m", "2d")
        if len(d) < 15:
            return "AGUARDA", False, 0, 0, 0
        
        d = adicionar_indicadores(d)
        if len(d) < 5:
            return "AGUARDA", False, 0, 0, 0
        
        preco = round(float(d["Close"].iloc[-1]), 5)
        high = round(float(d["High"].iloc[-1]), 5)
        low = round(float(d["Low"].iloc[-1]), 5)
        u = d.iloc[-1]
        corpo = float(u["corpo"])
        media_corpo = float(u["media_corpo"])
        e20 = float(d["ema20"].iloc[-1])
        
        if dr == "BUY":
            vela_ok = (float(u["Close"]) > float(u["Open"])) and (corpo > media_corpo * 0.8) and (preco > e20)
        else:
            vela_ok = (float(u["Close"]) < float(u["Open"])) and (corpo > media_corpo * 0.8) and (preco < e20)
        
        df2 = d.copy()
        df2["sh"] = ((df2["High"] > df2["High"].shift(1)) & 
                     (df2["High"] > df2["High"].shift(-1)) &
                     (df2["High"] > df2["High"].shift(2)) &
                     (df2["High"] > df2["High"].shift(-2)))
        df2["sl"] = ((df2["Low"] < df2["Low"].shift(1)) & 
                     (df2["Low"] < df2["Low"].shift(-1)) &
                     (df2["Low"] < df2["Low"].shift(2)) &
                     (df2["Low"] < df2["Low"].shift(-2)))
        
        sh = df2[df2["sh"]]["High"]
        sl = df2[df2["sl"]]["Low"]
        
        bos_m5 = False
        if len(sh) >= 1 and len(sl) >= 1:
            ush = float(sh.iloc[-1])
            usl = float(sl.iloc[-1])
            p_ant = float(d["Close"].iloc[-2])
            p_atu = float(d["Close"].iloc[-1])
            
            if dr == "BUY":
                bos_m5 = (p_atu > ush) and (p_ant > ush)
            else:
                bos_m5 = (p_atu < usl) and (p_ant < usl)
        
        if MODO == "CONSERVADOR":
            reteste_valido = (vela_ok or bos_m5) and bos_ok_m15
        else:
            reteste_valido = (vela_ok or bos_m5)
        
        estado = "CONFIRMADO" if reteste_valido else "AGUARDA"
        
        del d, df2
        gc.collect()
        return estado, reteste_valido, preco, high, low
    except Exception as e:
        gc.collect()
        return "AGUARDA", False, 0, 0, 0

# ==================== TIPO DE ORDEM ====================
def tipo_ordem(reteste_ok, liq_estado, vol_forte, dr):
    if reteste_ok:
        return "MARKET ORDER"
    elif liq_estado == "VARRIDA":
        return "BUY LIMIT" if dr == "BUY" else "SELL LIMIT"
    elif vol_forte:
        return "BUY STOP" if dr == "BUY" else "SELL STOP"
    else:
        return "BUY LIMIT" if dr == "BUY" else "SELL LIMIT"

# ==================== CÁLCULO DE RISCO ====================
def calc_risco(preco, dr, high_m5, low_m5, atr, par):
    p = float(preco)
    a = float(atr)
    eh_ouro = "GC" in par
    mult = 1.0 if eh_ouro else 0.5
    
    if dr == "BUY":
        sl = round(low_m5 - a * mult, 5) if low_m5 > 0 else round(p - a * 1.5, 5)
        if sl >= p:
            sl = round(p - a, 5)
        risco = abs(p - sl)
        tp1 = round(p + risco * 2, 5)
        tp2 = round(p + risco * 4, 5)
        if tp1 <= p:
            tp1 = round(p + a, 5)
    else:
        sl = round(high_m5 + a * mult, 5) if high_m5 > 0 else round(p + a * 1.5, 5)
        if sl <= p:
            sl = round(p + a, 5)
        risco = abs(sl - p)
        tp1 = round(p - risco * 2, 5)
        tp2 = round(p - risco * 4, 5)
        if tp1 >= p:
            tp1 = round(p - a, 5)
    
    r = abs(p - sl)
    pips = round(r, 2) if eh_ouro else round(r * 10000, 1)
    unidade = "USD" if eh_ouro else "pips"
    
    return {"sl": sl, "tp1": tp1, "tp2": tp2, "pips": pips, "unidade": unidade, "rr": f"1:{round(abs(p - tp2) / r, 1) if r > 0 else 0}"}

# ==================== ANÁLISE PRINCIPAL (CORRIGIDA) ====================
def analisar(par, ignorar_sessao=False):
    try:
        nome = nomes.get(par, par.replace("=X", "").replace("=F", ""))
        s = sessao_activa()
        sessao_ok = s["operar"] if not ignorar_sessao else True
        
        d15 = obter_dados(par, "15m", "7d")
        if len(d15) < 30:
            gc.collect()
            return None
        d15 = adicionar_indicadores(d15)
        if len(d15) < 10:
            gc.collect()
            return None
        
        preco_m15 = round(float(d15["Close"].iloc[-1]), 5)
        rsi = round(float(d15["rsi"].iloc[-1]), 1)
        atr = float(d15["atr"].iloc[-1])
        
        t_h1 = fase1_h1(par)
        
        p_m15 = float(d15["Close"].iloc[-1])
        e20_m15 = float(d15["ema20"].iloc[-1])
        tend_m15 = "BULLISH" if p_m15 > e20_m15 else "BEARISH"
        
        if t_h1 == "BULLISH" and tend_m15 == "BULLISH":
            dr = "BUY"
        elif t_h1 == "BEARISH" and tend_m15 == "BEARISH":
            dr = "SELL"
        elif t_h1 == "BULLISH":
            dr = "BUY"
        else:
            dr = "SELL"
        
        liq_estado, liq_ok, nivel_liq, _ = fase2_liquidez(d15, dr)
        bos_ok, choch_ok, tipo_bos = fase3_bos(d15, dr)
        volume, vol_forte = fase4_volume(d15, par)
        ret_estado, reteste_ok, preco_m5, high_m5, low_m5 = fase5_reteste_m5(par, dr, bos_ok)
        
        preco_entrada = preco_m15
        
        score = 0
        raz = []
        
        if liq_ok:
            score += 20
            raz.append("Liquidez varrida +20")
        if bos_ok:
            score += 15
            raz.append(tipo_bos + " M15 +15")
        if reteste_ok:
            score += 15
            raz.append("Reteste M5 +15")
        
        h1_alinhado = t_h1 == dr
        m15_alinhado = tend_m15 == dr
        
        if h1_alinhado and m15_alinhado:
            score += 20
            raz.append("H1+M15 alinhados +20")
        elif h1_alinhado:
            score += 10
            raz.append("H1 alinhado +10")
        
        if volume == "FORTE":
            score += 15
            raz.append("Volume forte +15")
        elif volume == "MEDIO":
            score += 8
            raz.append("Volume medio +8")
        
        if sessao_ok:
            score += s["score_bonus"]
            raz.append("Sessao " + s["sessao"])
        
        if choch_ok:
            score += 5
            raz.append("CHoCH +5")
        
        classificacao, emoji = classificar(score)
        ordem = tipo_ordem(reteste_ok, liq_estado, vol_forte, dr)
        r = calc_risco(preco_entrada, dr, 0, 0, atr, par)
        
        sinal = liq_ok and bos_ok and reteste_ok and score >= 60 and r["pips"] > 0
        
        result = {
            "par": nome,
            "score": score,
            "classificacao": classificacao,
            "emoji": emoji,
            "sinal": sinal,
            "dir": dr,
            "preco": preco_entrada,
            "preco_m15": preco_m15,
            "preco_m5": preco_m5,
            "sl": r["sl"],
            "tp1": r["tp1"],
            "tp2": r["tp2"],
            "rr": r["rr"],
            "pips": r["pips"],
            "unidade": r["unidade"],
            "rsi": rsi,
            "tend_h1": t_h1,
            "tend_m15": tend_m15,
            "volume": volume,
            "atr": round(atr, 5),
            "liq_estado": liq_estado,
            "liq_ok": liq_ok,
            "nivel_liq": nivel_liq,
            "bos_ok": bos_ok,
            "tipo_bos": tipo_bos,
            "ret_estado": ret_estado,
            "reteste_ok": reteste_ok,
            "ordem": ordem,
            "sessao": s["sessao"],
            "operar": s["operar"],
            "raz": raz
        }
        
        del d15
        gc.collect()
        return result
    except Exception as e:
        gc.collect()
        return None

# ==================== EMAIL ====================
def enviar_email_sinal(sinal):
    try:
        if not RESEND_OK:
            return
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        email_destino = os.environ.get("EMAIL_DESTINO", "")
        if not resend.api_key or not email_destino:
            return
        
        corpo = (
            sinal["emoji"] + " SINAL SMC — " + str(sinal["classificacao"]) + "\n"
            + "================================\n"
            + "Par:        " + sinal["par"] + "\n"
            + "Direccao:   " + sinal["dir"] + "\n"
            + "Ordem:      " + sinal["ordem"] + "\n"
            + "Entrada:    " + str(sinal["preco"]) + "\n"
            + "Score:      " + str(sinal["score"]) + "%\n"
            + "RSI:        " + str(sinal["rsi"]) + "\n"
            + "Volume:     " + sinal["volume"] + "\n"
            + "H1:         " + sinal["tend_h1"] + "\n"
            + "M15:        " + sinal["tend_m15"] + "\n\n"
            + "3 FASES OBRIGATORIAS\n"
            + "================================\n"
            + "Liquidez:   " + sinal["liq_estado"] + "\n"
            + "BOS/CHoCH:  " + sinal["tipo_bos"] + "\n"
            + "Reteste M5: " + sinal["ret_estado"] + "\n\n"
            + "GESTAO DE RISCO\n"
            + "================================\n"
            + "SL:    " + str(sinal["sl"]) + "\n"
            + "TP1:   " + str(sinal["tp1"]) + "\n"
            + "TP2:   " + str(sinal["tp2"]) + "\n"
            + "R:R:   " + sinal["rr"] + "\n"
            + "Risco: " + str(sinal["pips"]) + " " + sinal["unidade"] + "\n\n"
            + "\n".join(sinal["raz"])
        )
        
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": email_destino,
            "subject": sinal["emoji"] + " " + sinal["par"] + " " + sinal["dir"] + " | " + sinal["ordem"] + " @ " + str(sinal["preco"]) + " | " + str(sinal["score"]) + "% " + str(sinal["classificacao"]),
            "text": corpo
        })
    except Exception as e:
        pass

# ==================== MONITOR BACKGROUND ====================
sinais_enviados = set()

def monitor_background():
    global sinais_enviados
    while True:
        try:
            for par in pares:
                r = analisar(par)
                if r and r.get("sinal"):
                    chave = f"{r['par']}_{r['dir']}"
                    if chave not in sinais_enviados:
                        enviar_email_sinal(r)
                        sinais_enviados.add(chave)
                        if len(sinais_enviados) > 20:
                            sinais_enviados.clear()
                time.sleep(2)
        except Exception as e:
            pass
        time.sleep(300)
        gc.collect()

# ==================== GRÁFICO ====================
def grafico(par, dr, nivel, preco_entrada):
    try:
        d = obter_dados(par, "15m", "2d")
        if len(d) < 10:
            return None
        d = adicionar_indicadores(d)
        df = d.tail(60)
        
        fig = go.Figure(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], name=par,
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350"
        ))
        
        if "ema20" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["ema20"], name="EMA20", line=dict(color="orange", width=1)))
        if "ema50" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["ema50"], name="EMA50", line=dict(color="blue", width=1)))
        if "ema100" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["ema100"], name="EMA100", line=dict(color="red", width=1)))
        
        if nivel and nivel > 0:
            fig.add_hline(y=nivel, line_dash="dot", line_color="yellow", annotation_text="Liquidez")
        if preco_entrada and preco_entrada > 0:
            fig.add_hline(y=preco_entrada, line_dash="dash", line_color="white", annotation_text="Entrada")
        
        fig.update_layout(title=f"{par} M15", xaxis_rangeslider_visible=False, height=420, template="plotly_dark", showlegend=False)
        
        del d
        gc.collect()
        return fig
    except Exception as e:
        return None

# ==================== STREAMLIT UI ====================
if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = True
    th = threading.Thread(target=monitor_background, daemon=True)
    th.start()

st.title("SMC Scanner Pro")
st.caption("EURUSD | GBPUSD | USDJPY | XAUUSD | H1+M15+M5 | Monitor 24/7")

s = sessao_activa()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Sessao", s["sessao"])
c2.metric("Hora UTC", datetime.now(timezone.utc).strftime("%H:%M"))
c3.metric("Operar", "SIM" if s["operar"] else "NAO")
c4.metric("Ativos", "EURUSD GBPUSD USDJPY XAUUSD")
st.divider()

if st.button("Analisar mercado", type="primary"):
    resultados = []
    prog = st.progress(0)
    
    for i, par in enumerate(pares):
        r = analisar(par, ignorar_sessao=True)
        if r:
            resultados.append(r)
        prog.progress((i + 1) / len(pares))
        gc.collect()
    
    sinais = [r for r in resultados if r.get("sinal")]
    
    if sinais:
        st.success(f"{len(sinais)} SINAL(IS) ENCONTRADO(S)!")
        for sinal in sinais:
            with st.expander(
                sinal["emoji"] + " " + sinal["par"] + " " + sinal["dir"] + " | " + sinal["ordem"] + " @ " + str(sinal["preco"]) + " | " + str(sinal["score"]) + "% " + str(sinal["classificacao"]),
                expanded=True
            ):
                st.markdown(f"### {sinal['emoji']} {sinal['par']} — {sinal['dir']} — {sinal['classificacao']}")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Tipo Ordem", sinal["ordem"])
                col2.metric("Entrada", sinal["preco"])
                col3.metric("Score", f"{sinal['score']}%")
                col4.metric("Classe", sinal["classificacao"])
                
                col1b, col2b, col3b, col4b = st.columns(4)
                col1b.metric("Stop Loss", sinal["sl"])
                col2b.metric("TP1", sinal["tp1"])
                col3b.metric("TP2", sinal["tp2"])
                col4b.metric("Risco", f"{sinal['pips']} {sinal['unidade']}")
                
                col1c, col2c, col3c, col4c = st.columns(4)
                col1c.metric("H1", sinal["tend_h1"])
                col2c.metric("M15", sinal["tend_m15"])
                col3c.metric("Volume", sinal["volume"])
                col4c.metric("RSI", sinal["rsi"])
                
                st.markdown("**3 Fases Obrigatorias:**")
                cola, colb, colc = st.columns(3)
                cola.metric("Liquidez M15", sinal["liq_estado"])
                colb.metric("Estrutura M15", sinal["tipo_bos"])
                colc.metric("Reteste M5", sinal["ret_estado"])
                
                st.caption(f"R:R {sinal['rr']} | ATR: {sinal['atr']}")
                st.caption(" | ".join(sinal["raz"]))
                
                par_key = [p for p in pares if nomes.get(p, "") == sinal["par"]]
                fig = grafico(par_key[0] if par_key else sinal["par"], sinal["dir"], sinal.get("nivel_liq", 0), sinal["preco"])
                if fig:
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem sinais. Aguarda: Liquidez VARRIDA + BOS/CHoCH + Reteste M5 CONFIRMADO.")
    
    if resultados:
        st.subheader("Estado do mercado")
        linhas = []
        for r in resultados:
            obrig = sum([r.get("liq_ok", False), r.get("bos_ok", False), r.get("reteste_ok", False)])
            if r.get("sinal"):
                status = r.get("emoji", "") + " " + str(r.get("classificacao", ""))
            elif obrig == 2:
                status = "QUASE (2/3)"
            elif obrig == 1:
                status = "A FORMAR (1/3)"
            else:
                status = "AGUARDA"
            
            linhas.append({
                "Status": status,
                "Par": r["par"],
                "Dir": r.get("dir", ""),
                "Score": str(r.get("score", 0)) + "%",
                "RSI": r.get("rsi", 0),
                "H1": r.get("tend_h1", ""),
                "M15": r.get("tend_m15", ""),
                "Volume": r.get("volume", ""),
                "Liquidez": r.get("liq_estado", ""),
                "BOS/CHoCH": r.get("tipo_bos", ""),
                "Reteste M5": r.get("ret_estado", ""),
                "Entrada": r.get("preco", 0),
                "SL": r.get("sl", 0),
                "TP1": r.get("tp1", 0),
                "TP2": r.get("tp2", 0)
            })
        
        st.dataframe(pd.DataFrame(linhas), use_container_width=True)

st.caption(f"Modo: {MODO} | Actualizado: {datetime.now().strftime('%H:%M:%S')} | PREMIUM>=85% NORMAL>=70% ACEITAVEL>=60%")
