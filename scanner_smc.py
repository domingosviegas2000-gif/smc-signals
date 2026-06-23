import yfinance as yf
import pandas as pd
import numpy as np
import gc
import requests
from datetime import datetime, timezone

pares_smc = ["EURUSD=X","GBPUSD=X","USDJPY=X","GC=F","BTC-USD"]
nomes_smc = {"EURUSD=X":"EURUSD","GBPUSD=X":"GBPUSD","USDJPY=X":"USDJPY","GC=F":"XAUUSD","BTC-USD":"BTCUSD"}

def obter_dados_smc(par, intervalo="15m", periodo="7d"):
    try:
        d = yf.download(par, period=periodo, interval=intervalo, auto_adjust=True, progress=False)
        d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
        return d.dropna()
    except: return pd.DataFrame()

def add_ind_smc(df):
    if len(df)<20: return df
    df = df.copy()
    df["corpo"] = abs(df["Close"]-df["Open"])
    df["media_corpo"] = df["corpo"].rolling(20).mean()
    return df.dropna()

def precisao(par):
    if "GC" in par: return 2
    if "BTC" in par: return 0
    return 5

def formatar_idade(hora_criacao):
    try:
        agora = datetime.now(timezone.utc)
        hc = hora_criacao.to_pydatetime()
        if hc.tzinfo is None: hc = hc.replace(tzinfo=timezone.utc)
        diff = agora - hc
        horas = int(diff.total_seconds()//3600)
        minutos = int((diff.total_seconds()%3600)//60)
        return f"{horas}h {minutos}m"
    except: return "N/D"

# ==================== CONTEXTO MACRO ====================
def obter_contexto_macro():
    try:
        dxy = yf.download("DX-Y.NYB", period="5d", interval="1d", progress=False, auto_adjust=True)
        dxy.columns = [c[0] if isinstance(c, tuple) else c for c in dxy.columns]
        if len(dxy)>=2:
            var = (float(dxy["Close"].iloc[-1])-float(dxy["Close"].iloc[-2]))/float(dxy["Close"].iloc[-2])*100
            usd = "FORTE" if var>0.15 else "FRACO" if var<-0.15 else "NEUTRO"
        else: usd = "NEUTRO"
    except: usd = "NEUTRO"

    try:
        ouro = yf.download("GC=F", period="5d", interval="1d", progress=False, auto_adjust=True)
        ouro.columns = [c[0] if isinstance(c, tuple) else c for c in ouro.columns]
        if len(ouro)>=2:
            var = (float(ouro["Close"].iloc[-1])-float(ouro["Close"].iloc[-2]))/float(ouro["Close"].iloc[-2])*100
            ouro_s = "FORTE" if var>0.3 else "FRACO" if var<-0.3 else "NEUTRO"
        else: ouro_s = "NEUTRO"
    except: ouro_s = "NEUTRO"

    try:
        spy = yf.download("SPY", period="5d", interval="1d", progress=False, auto_adjust=True)
        spy.columns = [c[0] if isinstance(c, tuple) else c for c in spy.columns]
        if len(spy)>=2:
            var = (float(spy["Close"].iloc[-1])-float(spy["Close"].iloc[-2]))/float(spy["Close"].iloc[-2])*100
            risco = "FORTE" if var>0.3 else "FRACO" if var<-0.3 else "NEUTRO"
        else: risco = "NEUTRO"
    except: risco = "NEUTRO"

    proximo_evento = None
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=5)
        if r.status_code==200:
            eventos = r.json()
            agora = datetime.now(timezone.utc)
            futuros = []
            for ev in eventos:
                if ev.get("impact","")!="High": continue
                try:
                    ev_time = datetime.strptime(ev["date"],"%Y-%m-%dT%H:%M:%S%z")
                    if ev_time>agora:
                        diff_min=(ev_time-agora).total_seconds()/60
                        futuros.append({"nome":ev.get("title",""),"minutos":diff_min})
                except: continue
            if futuros:
                futuros.sort(key=lambda x: x["minutos"])
                ev=futuros[0]; h=int(ev["minutos"]//60); m=int(ev["minutos"]%60)
                tempo=f"{h}h {m}m" if h>0 else f"{m}m"
                proximo_evento=f"{ev['nome']} em {tempo}"
    except: pass

    return {"usd":usd,"ouro":ouro_s,"risco":risco,"proximo_evento":proximo_evento}

# ==================== ESTRUTURA CORRIGIDA ====================
def detectar_estrutura(df, par):
    """
    Classifica estrutura correctamente:
    HH+HL = ALTA
    LH+LL = BAIXA
    HH+LL = TRANSICAO
    LH+HL = CONSOLIDACAO
    """
    p_round = precisao(par)
    df2 = df.copy()
    df2["sh"]=((df2["High"]>df2["High"].shift(1))&(df2["High"]>df2["High"].shift(-1))&
               (df2["High"]>df2["High"].shift(2))&(df2["High"]>df2["High"].shift(-2)))
    df2["sl"]=((df2["Low"]<df2["Low"].shift(1))&(df2["Low"]<df2["Low"].shift(-1))&
               (df2["Low"]<df2["Low"].shift(2))&(df2["Low"]<df2["Low"].shift(-2)))
    sh=df2[df2["sh"]]; sl=df2[df2["sl"]]
    if len(sh)<2 or len(sl)<2:
        return {"estrutura":"INDEFINIDA","estado":"SEM DADOS","pontos":[],"bos":False,"choch":False}

    ush=float(sh["High"].iloc[-1]); psh=float(sh["High"].iloc[-2])
    usl=float(sl["Low"].iloc[-1]);  psl=float(sl["Low"].iloc[-2])
    hora_sh=sh.index[-1]; hora_sl=sl.index[-1]
    p=float(df["Close"].iloc[-1]); pp=float(df["Close"].iloc[-2])

    hh = ush>psh  # topo mais alto
    lh = ush<psh  # topo mais baixo
    hl = usl>psl  # fundo mais alto
    ll = usl<psl  # fundo mais baixo

    # Classificacao correcta
    if hh and hl:   estrutura="ALTA"
    elif lh and ll: estrutura="BAIXA"
    elif hh and ll: estrutura="TRANSICAO"
    elif lh and hl: estrutura="CONSOLIDACAO"
    else:           estrutura="INDEFINIDA"

    pontos = []
    if hh: pontos.append({"tipo":"HH","preco":round(ush,p_round),"hora":hora_sh})
    else:  pontos.append({"tipo":"LH","preco":round(ush,p_round),"hora":hora_sh})
    if hl: pontos.append({"tipo":"HL","preco":round(usl,p_round),"hora":hora_sl})
    else:  pontos.append({"tipo":"LL","preco":round(usl,p_round),"hora":hora_sl})

    # BOS e CHoCH
    bos_bull=(p>ush)and(pp>ush)
    bos_bear=(p<usl)and(pp<usl)
    bos=bos_bull or bos_bear
    choch=(bos_bull and estrutura=="BAIXA") or (bos_bear and estrutura=="ALTA")

    if bos_bull:
        tipo="CHoCH" if choch else "BOS"
        pontos.append({"tipo":tipo+" ALTA","preco":round(p,p_round),"hora":df.index[-1]})
    if bos_bear:
        tipo="CHoCH" if choch else "BOS"
        pontos.append({"tipo":tipo+" BAIXA","preco":round(p,p_round),"hora":df.index[-1]})

    # Estado
    if choch: estado="REVERSAO"
    elif bos: estado="EXPANSAO"
    elif estrutura=="ALTA": estado="CORRECAO"
    elif estrutura=="BAIXA": estado="CORRECAO"
    else: estado="INDEFINIDO"

    return {"estrutura":estrutura,"estado":estado,"pontos":pontos,"bos":bos,"choch":choch,
            "ush":round(ush,p_round),"usl":round(usl,p_round)}

# ==================== LIQUIDEZ CORRIGIDA ====================
def detectar_liquidez(df, par):
    """
    BSL varrida: High recente > BSL (nao apenas tocar)
    SSL varrida: Low recente < SSL (nao apenas tocar)
    """
    p_round = precisao(par)
    h30=df["High"].tail(30); l30=df["Low"].tail(30)
    p=float(df["Close"].iloc[-1])
    bsl=round(float(h30.max()),p_round)
    ssl=round(float(l30.min()),p_round)

    # EQH e EQL
    highs=h30.nlargest(5); lows=l30.nsmallest(5)
    eqh=None
    for i in range(len(highs)):
        for j in range(i+1,len(highs)):
            v1,v2=highs.iloc[i],highs.iloc[j]
            if abs(v1-v2)/v1<0.0008: eqh=round((v1+v2)/2,p_round); break
        if eqh: break
    eql=None
    for i in range(len(lows)):
        for j in range(i+1,len(lows)):
            v1,v2=lows.iloc[i],lows.iloc[j]
            if abs(v1-v2)/v1<0.0008: eql=round((v1+v2)/2,p_round); break
        if eql: break

    # CORRIGIDO: varredura requer ultrapassagem real
    h5=df["High"].tail(5); l5=df["Low"].tail(5)
    bsl_varrida=float(h5.max())>bsl    # High deve SUPERAR BSL
    ssl_varrida=float(l5.min())<ssl    # Low deve ESTAR ABAIXO de SSL

    # Grab: varreu e voltou
    grab_topo=bsl_varrida and p<bsl
    grab_fundo=ssl_varrida and p>ssl

    return {"bsl":bsl,"ssl":ssl,"eqh":eqh,"eql":eql,
            "bsl_varrida":bsl_varrida,"ssl_varrida":ssl_varrida,
            "grab_topo":grab_topo,"grab_fundo":grab_fundo}

# ==================== ORDER BLOCKS ====================
def detectar_order_blocks(df, par, tf_label):
    p_round=precisao(par)
    df=df.copy()
    df["vf"]=df["corpo"]>df["media_corpo"]*1.4
    p=float(df["Close"].iloc[-1])
    blocks=[]

    for tipo_ob in ["BULLISH","BEARISH"]:
        for i in range(len(df)-3,max(len(df)-40,0),-1):
            if not df["vf"].iloc[i]: continue
            candle=df.iloc[i]; hora_criacao=df.index[i]
            if tipo_ob=="BULLISH":
                cond_ob=float(candle["Close"])<float(candle["Open"])
                cond_ativo=p>float(candle["High"])
                invalido=p<float(candle["Low"])
            else:
                cond_ob=float(candle["Close"])>float(candle["Open"])
                cond_ativo=p<float(candle["Low"])
                invalido=p>float(candle["High"])
            if cond_ob and cond_ativo and not invalido:
                alto=round(float(candle["High"]),p_round)
                baixo=round(float(candle["Low"]),p_round)
                pos=df.iloc[i+1:]
                testes=int(((pos["Low"]<=alto)&(pos["High"]>=baixo)).sum())
                forca=max(50,95-testes*8)
                if forca>=70:
                    nome="Bloco de Ordem Altista" if tipo_ob=="BULLISH" else "Bloco de Ordem Baixista"
                    blocks.append({"tipo":nome,"tf":tf_label,"alto":alto,"baixo":baixo,
                                   "hora_criacao":hora_criacao,"testes":testes,
                                   "forca":int(forca),"estado":"VALIDO"})
                break
    return blocks

# ==================== FVG CORRIGIDO ====================
def detectar_fvg(df, par, tf_label):
    """
    Estado baseado na posicao actual do preco vs topo/base
    FVG Altista: gap entre candle[i-1].High e candle[i+1].Low
    - ABERTO: preco > topo do gap (nunca mitigou)
    - PARCIAL: preco esta dentro do gap
    - PREENCHIDO: preco <= base do gap
    """
    p_round=precisao(par)
    p=float(df["Close"].iloc[-1])
    fvgs=[]

    for direcao in ["BULL","BEAR"]:
        for i in range(len(df)-2,max(len(df)-30,1),-1):
            if i+1>=len(df): continue
            if direcao=="BULL" and float(df["Low"].iloc[i+1])>float(df["High"].iloc[i-1]):
                topo=round(float(df["Low"].iloc[i+1]),p_round)
                base=round(float(df["High"].iloc[i-1]),p_round)
                # FVG altista:
                # PREENCHIDO: preco caiu abaixo da base OU subiu acima do topo
                # PARCIAL: preco esta dentro do gap
                # ABERTO: preco ainda nao entrou no gap
                if p<=base or p>=topo: estado="PREENCHIDO"
                elif base<p<topo: estado="PARCIAL"
                else: estado="ABERTO"
                if estado!="PREENCHIDO":
                    fvgs.append({"tipo":"Gap de Valor Justo Altista","tf":tf_label,
                                 "topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
                break
            elif direcao=="BEAR" and float(df["High"].iloc[i+1])<float(df["Low"].iloc[i-1]):
                topo=round(float(df["Low"].iloc[i-1]),p_round)
                base=round(float(df["High"].iloc[i+1]),p_round)
                # FVG baixista:
                # PREENCHIDO: preco subiu acima do topo OU caiu abaixo da base
                # PARCIAL: preco esta dentro do gap
                # ABERTO: preco ainda nao entrou no gap
                if p>=topo or p<=base: estado="PREENCHIDO"
                elif base<p<topo: estado="PARCIAL"
                else: estado="ABERTO"
                if estado!="PREENCHIDO":
                    fvgs.append({"tipo":"Gap de Valor Justo Baixista","tf":tf_label,
                                 "topo":topo,"base":base,"estado":estado,"hora":df.index[i]})
                break
    return fvgs

# ==================== NIVEIS INSTITUCIONAIS ====================
def niveis_institucionais(par):
    p_round=precisao(par)
    try:
        dd=obter_dados_smc(par,"1d","10d")
        pdh=round(float(dd["High"].iloc[-2]),p_round)
        pdl=round(float(dd["Low"].iloc[-2]),p_round)
        dw=obter_dados_smc(par,"1wk","10wk")
        pwh=round(float(dw["High"].iloc[-2]),p_round)
        pwl=round(float(dw["Low"].iloc[-2]),p_round)
        return {"pdh":pdh,"pdl":pdl,"pwh":pwh,"pwl":pwl}
    except: return {"pdh":0,"pdl":0,"pwh":0,"pwl":0}

# ==================== FLUXO INSTITUCIONAL H1 ====================
def fluxo_institucional_h1(par):
    try:
        d=obter_dados_smc(par,"1h","10d")
        if len(d)<30: return "INDEFINIDO"
        d=add_ind_smc(d)
        res=detectar_estrutura(d,par)
        return res["estrutura"]
    except: return "INDEFINIDO"

# ==================== QUALIDADE DA ESTRUTURA ====================
def calcular_qualidade(fluxo_h1, est_m15, est_m5, macro_alinhado):
    alinhados = sum([
        fluxo_h1==est_m15["estrutura"],
        est_m15["estrutura"]==est_m5["estrutura"],
        fluxo_h1==est_m5["estrutura"]
    ])
    if macro_alinhado and alinhados==3: return "A+"
    elif alinhados==3: return "A"
    elif fluxo_h1==est_m15["estrutura"]: return "B"
    elif est_m15["estrutura"]==est_m5["estrutura"]: return "C"
    else: return "D"

# ==================== CONTEXTO MACRO POR PAR ====================
def contexto_macro_par(par, macro, fluxo_h1):
    try:
        if "GC" in par:
            if macro["ouro"]=="FORTE" and fluxo_h1=="ALTA": return "ALINHADO"
            elif macro["ouro"]=="FRACO" and fluxo_h1=="BAIXA": return "ALINHADO"
            elif macro["ouro"]=="NEUTRO": return "NEUTRO"
            else: return "CONTRA"
        elif "BTC" in par:
            if macro["risco"]=="FORTE" and fluxo_h1=="ALTA": return "ALINHADO"
            elif macro["risco"]=="FRACO" and fluxo_h1=="BAIXA": return "ALINHADO"
            else: return "NEUTRO"
        else:
            if macro["usd"]=="FORTE" and fluxo_h1=="BAIXA": return "ALINHADO"
            elif macro["usd"]=="FRACO" and fluxo_h1=="ALTA": return "ALINHADO"
            elif macro["usd"]=="NEUTRO": return "NEUTRO"
            else: return "CONTRA"
    except: return "NEUTRO"

# ==================== FASE DE MERCADO CORRIGIDA ====================
def determinar_fase(est_m15, liq, obs, fvgs):
    """
    Fase corrigida: nao classifica MANIPULACAO apenas com liquidez.
    Requer confirmacao de BOS ou CHoCH apos grab.
    """
    grab=liq["grab_topo"] or liq["grab_fundo"]
    bos=est_m15["bos"]
    choch=est_m15["choch"]
    estrutura=est_m15["estrutura"]
    estado=est_m15["estado"]
    tem_ob=len(obs)>0
    tem_fvg=len([f for f in fvgs if f["estado"]=="ABERTO"])>0

    if grab and choch:
        return "MANIPULACAO","Varredura de liquidez seguida de mudanca de carater. Possivel reversao."
    elif grab and bos:
        return "MANIPULACAO","Varredura de liquidez seguida de rompimento. Continuacao provavel."
    elif grab and not bos and not choch:
        return "MANIPULACAO","Varredura de liquidez detectada. Aguardar confirmacao estrutural (BOS ou CHoCH)."
    elif choch:
        return "REVERSAO","Mudanca de carater confirmada. Estrutura a inverter."
    elif bos and estrutura=="ALTA":
        return "EXPANSAO","Rompimento altista confirmado. Continuacao de estrutura de alta."
    elif bos and estrutura=="BAIXA":
        return "EXPANSAO","Rompimento baixista confirmado. Continuacao de estrutura de baixa."
    elif estrutura in ["ALTA","BAIXA"] and estado=="CORRECAO":
        return "CORRECAO","Preco em correcao dentro da estrutura dominante."
    elif estrutura=="TRANSICAO":
        return "TRANSICAO","Estrutura indefinida. HH com LL — expansao dos extremos."
    elif estrutura=="CONSOLIDACAO":
        return "ACUMULACAO","LH com HL — mercado em compressao. Aguardar rompimento."
    elif tem_ob and tem_fvg:
        return "CORRECAO","Preco em zona institucional com gaps activos."
    else:
        return "INDEFINIDA","Sem eventos estruturais relevantes no momento."

# ==================== SCANNER PRINCIPAL ====================
def escanear_par(par, macro):
    nome=nomes_smc[par]
    try:
        d15=obter_dados_smc(par,"15m","5d")
        if len(d15)<30: return None
        d15=add_ind_smc(d15)
        dh1=obter_dados_smc(par,"1h","10d")
        dh1=add_ind_smc(dh1)
        dm5=obter_dados_smc(par,"5m","2d")
        dm5=add_ind_smc(dm5)

        fluxo_h1=fluxo_institucional_h1(par)
        est_m15=detectar_estrutura(d15,par)
        est_m5=detectar_estrutura(dm5,par) if len(dm5)>30 else {"estrutura":"INDEFINIDA","estado":"SEM DADOS","pontos":[],"bos":False,"choch":False}
        liquidez=detectar_liquidez(d15,par)

        ob_h1=detectar_order_blocks(dh1,par,"H1") if len(dh1)>30 else []
        ob_m15=detectar_order_blocks(d15,par,"M15")
        fvg_h1=detectar_fvg(dh1,par,"H1") if len(dh1)>30 else []
        fvg_m15=detectar_fvg(d15,par,"M15")
        fvg_m5=detectar_fvg(dm5,par,"M5") if len(dm5)>30 else []

        niveis=niveis_institucionais(par)
        ctx_macro=contexto_macro_par(par,macro,fluxo_h1)
        macro_alinhado=ctx_macro=="ALINHADO"
        qualidade=calcular_qualidade(fluxo_h1,est_m15,est_m5,macro_alinhado)

        todos_obs=ob_h1+ob_m15
        todos_fvgs=fvg_h1+fvg_m15+fvg_m5

        for ob in todos_obs: ob["idade"]=formatar_idade(ob["hora_criacao"])
        for f in todos_fvgs: f["idade"]=formatar_idade(f["hora"])

        obs_validos=[o for o in todos_obs]
        fvgs_ativos=[f for f in todos_fvgs if f["estado"]!="PREENCHIDO"]

        fase,fase_desc=determinar_fase(est_m15,liquidez,obs_validos,fvgs_ativos)

        gc.collect()
        return {
            "par":nome,
            "fluxo_h1":fluxo_h1,
            "est_m15":est_m15,
            "est_m5":est_m5,
            "ctx_macro":ctx_macro,
            "qualidade":qualidade,
            "fase":fase,"fase_desc":fase_desc,
            "liquidez":liquidez,
            "ob_h1":ob_h1,"ob_m15":ob_m15,
            "fvg_h1":fvg_h1,"fvg_m15":fvg_m15,"fvg_m5":fvg_m5,
            "niveis":niveis,
            "ob_validos":len(obs_validos),
            "fvg_abertos":len([f for f in todos_fvgs if f["estado"]=="ABERTO"]),
            "liq_nao_varrida":sum([not liquidez["bsl_varrida"],not liquidez["ssl_varrida"],
                                   liquidez["eqh"] is not None,liquidez["eql"] is not None])
        }
    except:
        gc.collect()
        return None
