import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Load credentials from GitHub Secret
google_credentials = json.loads(
    os.environ["GOOGLE_CREDENTIALS"]
)

creds = Credentials.from_service_account_info(
    google_credentials,
    scopes=["https://www.googleapis.com/auth/drive"]
)

drive_service = build("drive", "v3", credentials=creds)

print("Connected to Google Drive!")

# from google.colab import drive
# drive.mount('/content/drive', force_remount=True)

import re
import pandas as pd
import pdfplumber
from pypdf import PdfReader, PdfWriter
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from calendar import monthrange

BASE_ORIGEM = "/content/drive/MyDrive/Dados/01-Brutos"
BASE_LIMPOS = "/content/drive/MyDrive/Dados/02-Limpos"
BASE_OUTPUT = "/content/drive/MyDrive/Dados/03-Output"

UC_GERADORA = "109441346"
os.makedirs(BASE_OUTPUT, exist_ok=True)

"""# **2. HELPERS |  Funções auxiliares (conversão, validação)**"""

def to_float(s: str) -> float:
    """Converte string numérica para float."""
    if s is None or s == "":
        return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def extrair_mes_ano_fatura(nome_arquivo: str) -> Tuple[Optional[int], Optional[int]]:
    """Extrai (ano, mes) da EMISSÃO do PDF."""
    m = re.search(r"COPEL-(\d{2})-(\d{4})", nome_arquivo)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None, None

def calcular_mes_referencia(ano_emissao: int, mes_emissao: int) -> Tuple[int, int]:
    """Calcula mês de referência (mês ANTERIOR)."""
    if mes_emissao == 1:
        return ano_emissao - 1, 12
    else:
        return ano_emissao, mes_emissao - 1

def arquivo_valido(nome: str) -> bool:
    """Verifica se arquivo é válido (após set/2024)."""
    ano, mes = extrair_mes_ano_fatura(nome)
    if ano is None:
        return False
    return (ano > 2024) or (ano == 2024 and mes >= 9)

def tem_energia_injetada(texto: str) -> bool:
    """Verifica se página tem energia injetada."""
    t = texto.lower()
    padroes = [
        r"energia\s+injetada",
        r"energia\s+inj",
        r"injetada",
        r"inj\s*kwh",
        r"energia\s+compensada"
    ]
    return any(re.search(p, t) for p in padroes)

def contem_uc_geradora(texto: str) -> bool:
    """Verifica se página contém a UC geradora."""
    return UC_GERADORA in texto


def extrair_numero_fatura(texto: str) -> Optional[str]:
    """Extrai número da fatura - VERSÃO FINAL CORRIGIDA"""

    # PADRÃO PRIORITÁRIO: FAT-01-XXXXXXXXXXXXX-XX
    # Exemplo: FAT-01-20263507013821-11
    m = re.search(r"FAT-\d+-(\d+)-\d+", texto, re.IGNORECASE)
    if m:
        return m.group(1)

    # PADRÃO 2: FAT-01-XXXXXXXXXXXXX.XX (com ponto)
    m = re.search(r"FAT-\d+-(\d+)\.\d+", texto, re.IGNORECASE)
    if m:
        return m.group(1)

    # PADRÃO 3: FAT com 14 dígitos
    m = re.search(r"FAT[-.]?\d+[-.](\d{14})", texto, re.IGNORECASE)
    if m:
        return m.group(1)

    # PADRÃO 4: Procurar após palavras-chave
    for padrao in [
        r"(?:NOTA FISCAL|NFe|NF-e|Nº|N°|FATURA)[:\s]*[N°]*[:\s]*(\d{14})",
        r"(?:Número|Numero)[:\s]+(\d{14})",
    ]:
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            return m.group(1)

    # PADRÃO 5: 14 dígitos isolados começando com 2026
    m = re.search(r"\b(202[4-9]\d{10})\b", texto)
    if m:
        return m.group(1)

    return None


def verificar_uso_credito(texto: str) -> int:
    """Verifica se a UC usou créditos no mês (Flag Uso Crédito)."""
    if "SALDO DE CREDITOS" in texto.upper():
        return 1
    return 0


print("✅ Funções auxiliares carregadas")

"""# **3. LIMPEZA | Filtra PDFs (apenas páginas relevantes)**"""

def extrair_paginas_filtradas(caminho_pdf: str, caminho_saida: str) -> bool:
    """Extrai apenas páginas relevantes."""
    try:
        reader = PdfReader(caminho_pdf)
    except Exception as e:
        print(f"❌ Erro ao abrir PDF: {caminho_pdf} - {e}")
        return False

    writer = PdfWriter()
    paginas_encontradas = 0

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    texto = page.extract_text() or ""
                    if not texto.strip():
                        continue

                    tem_uc = contem_uc_geradora(texto)
                    tem_inj = tem_energia_injetada(texto)

                    if tem_uc or tem_inj:
                        writer.add_page(reader.pages[i])
                        paginas_encontradas += 1
                except Exception:
                    continue

    except Exception as e:
        print(f"❌ Erro no pdfplumber: {caminho_pdf} - {e}")
        return False

    if paginas_encontradas == 0:
        print(f"⏭️  Sem páginas válidas: {os.path.basename(caminho_pdf)}")
        return False

    os.makedirs(os.path.dirname(caminho_saida), exist_ok=True)
    with open(caminho_saida, "wb") as f:
        writer.write(f)

    print(f"✅ {os.path.basename(caminho_pdf)} → {paginas_encontradas} páginas")
    return True

def processar_limpeza(teste_mes_ano: Optional[Tuple[int, int]] = None):
    """Processa limpeza de todos os PDFs."""
    processados = 0
    ignorados = 0

    arquivos = []
    for root, _, files in os.walk(BASE_ORIGEM):
        for file in files:
            if file.lower().endswith(".pdf"):
                caminho = os.path.join(root, file)
                arquivos.append((file, caminho))

    arquivos_ordenados = sorted(arquivos, key=lambda x: extrair_mes_ano_fatura(x[0]))

    if teste_mes_ano:
        ano_teste, mes_teste = teste_mes_ano
        arquivos_ordenados = [
            (f, c) for f, c in arquivos_ordenados
            if extrair_mes_ano_fatura(f) == (ano_teste, mes_teste)
        ]
        print(f"\n🧪 MODO TESTE: Apenas {ano_teste}/{mes_teste:02d}")

    total_arquivos = len(arquivos_ordenados)
    print(f"\n📊 Total de arquivos a processar: {total_arquivos}\n")

    for i, (file, caminho_origem) in enumerate(arquivos_ordenados, 1):
        print(f"\n[{i}/{total_arquivos}] 🔄 {file}")

        if not arquivo_valido(file):
            print("  ⏭️  Ignorado (fora do período)")
            ignorados += 1
            continue

        rel_path = os.path.relpath(caminho_origem, BASE_ORIGEM)
        caminho_destino = os.path.join(BASE_LIMPOS, rel_path)

        if os.path.exists(caminho_destino):
            print("  ⏭️  Já processado")
            continue

        try:
            ok = extrair_paginas_filtradas(caminho_origem, caminho_destino)
            if ok:
                processados += 1
            else:
                ignorados += 1
        except Exception as e:
            print(f"  ❌ Erro: {e}")
            ignorados += 1

    print("\n" + "="*60)
    print(f"📄 Total: {total_arquivos}")
    print(f"✅ Processados: {processados}")
    print(f"⏭️  Ignorados: {ignorados}")
    print("="*60)

"""# **4. EXTRAÇÃO | Extrai dados com regex otimizado**"""

# **EXTRAÇÃO - FUNÇÕES AUXILIARES**

def extrair_uc_beneficiaria(texto: str) -> Optional[str]:
    """Extrai número da UC beneficiária."""
    linhas = texto.split("\n")

    for i, linha in enumerate(linhas):
        if "UNIDADE CONSUMIDORA" in linha.upper():
            for j in range(i, min(i+4, len(linhas))):
                nums = re.findall(r"^\s*(\d{7,9})\s*$", linhas[j])
                if nums:
                    return nums[0]

    for linha in linhas:
        if linha.startswith("Endereço:") or "Endere" in linha:
            parte_antes_nota = linha.split("NOTA FISCAL")[0]
            nums = re.findall(r"\b(\d{7,9})\b", parte_antes_nota)
            if nums:
                return nums[-1]

    for i, linha in enumerate(linhas[:10]):
        if i < 10:
            nums = re.findall(r"^\s*(\d{8,9})\s*$", linha)
            if nums:
                return nums[0]

    for linha in linhas:
        if "Endereço:" in linha or "Endere" in linha:
            parte_antes_nota = linha.split("NOTA FISCAL")[0]
            nums = re.findall(r"\b(\d{6,9})\b", parte_antes_nota)
            if nums:
                return nums[-1]

    return None

def extrair_grupo_tensao(texto: str) -> Optional[str]:
    """Extrai Grupo de Tensão - Aceita A3a, A3e, A4, B3, etc."""
    linhas = texto.split("\n")

    palavras_chave = [
        "Poder Publico", "Comercial", "Industrial", "Rural",
        "Residencial", "Servico Publico", "Ppe-Justica"
    ]

    for linha in linhas:
        tem_palavra_chave = any(palavra.lower() in linha.lower() for palavra in palavras_chave)

        if tem_palavra_chave:
            m = re.search(r"\b([AB]\d+[a-z]*)\b", linha, re.IGNORECASE)
            if m:
                return m.group(1).upper()

    m = re.search(r"Classifica[çc][ãa]o:\s*([AB]\d+[a-z]*)", texto, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    for i, linha in enumerate(linhas):
        if "Classificação" in linha or "Classificacao" in linha:
            m = re.search(r"([AB]\d+[a-z]*)", linha, re.IGNORECASE)
            if m:
                return m.group(1).upper()

            for j in range(i+1, min(i+4, len(linhas))):
                m = re.match(r"^([AB]\d+[a-z]*)", linhas[j].strip(), re.IGNORECASE)
                if m:
                    return m.group(1).upper()

    for linha in linhas:
        if any(palavra in linha.lower() for palavra in ["trifasico", "bifasico", "monofasico", "tarif", "tensao"]):
            m = re.search(r"\b([AB]\d+[a-z]*)\b", linha, re.IGNORECASE)
            if m:
                return m.group(1).upper()

    return None

def extrair_saldo_credito_anterior(texto: str) -> float:
    """Extrai saldo de créditos da fatura anterior."""
    m = re.search(r"SALDO DE CREDITOS FATURA ANTERIOR.*?(-?[\d.,]+)", texto)
    if m:
        return to_float(m.group(1))
    return 0.0

def extrair_saldo_creditos_devolver(texto: str) -> Tuple[float, float, float, float]:
    """Extrai SALDO DE CREDITOS e SALDO A DEVOLVER."""
    saldo_cred_kwh = 0.0
    saldo_cred_rs = 0.0
    saldo_dev_kwh = 0.0
    saldo_dev_rs = 0.0

    m = re.search(r"SALDO DE CREDITOS.*?(-?[\d.,]+)\s+kWh\s+(-?[\d.,]+)", texto)
    if m:
        saldo_cred_kwh = abs(to_float(m.group(1)))
        saldo_cred_rs = abs(to_float(m.group(2)))

    m = re.search(r"SALDO A DEVOLVER.*?(-?[\d.,]+)\s+kWh\s+(-?[\d.,]+)", texto)
    if m:
        saldo_dev_kwh = abs(to_float(m.group(1)))
        saldo_dev_rs = abs(to_float(m.group(2)))

    return saldo_cred_kwh, saldo_cred_rs, saldo_dev_kwh, saldo_dev_rs

def extrair_consumo_compensado_texto(texto: str) -> Tuple[float, float]:
    """Extrai consumo compensado do texto especial."""
    padrao = re.compile(
        r"CONSUMO COMPENSADO.*?"
        r"\(PONTA\s+([\d.,]+)\s+kWh,\s+"
        r"F\s*PONTA\s+([\d.,]+)\s+kWh",
        re.IGNORECASE | re.DOTALL
    )

    m = padrao.search(texto)
    if m:
        kwh_pt = to_float(m.group(1))
        kwh_fp = to_float(m.group(2))
        return kwh_pt, kwh_fp

    return 0.0, 0.0



# **4. EXTRAÇÃO - SUBSTITUIR COMPLETAMENTE extrair_valores_energia_inj**
def extrair_valores_energia_inj(texto: str, padrao_busca: str = "ENERGIA INJ") -> Tuple[float, float, List[str]]:
    """
    ═══════════════════════════════════════════════════════════════════════════
    EXTRAÇÃO DE VALORES DE ENERGIA INJ - VERSÃO FINAL CORRIGIDA V3
    ═══════════════════════════════════════════════════════════════════════════

    SUPORTA DOIS FORMATOS:

    FORMATO 1 (Página 10 - LIMPO - PRIORIZADO):
        ENERGIA INJ. PT OUC 02/2026 GDI-I -2291,00 0,455853 -1.044,36
        Extração: Posição 0 = kWh, Posição 2 = Valor R$

    FORMATO 2 (Página 9 - SUJO):
        ENERGIA INJ. PT OUC MPT TE 02/2026 GDI-kIWh -2.291 0,455853 -1.044,36 -96,60 0,00 0,413690
        Extração: Posição 0 = kWh, Posição 2 = Valor R$

    DISTINCT ESTRATÉGIA:
    - kWh: DISTINCT por (mês, kWh) → mesmo kWh em 2 linhas = conta 1x só
    - Valor R$: DISTINCT por (mês, kWh, valor) → cada cálculo diferente soma
    ═══════════════════════════════════════════════════════════════════════════
    """
    total_kwh = 0.0
    total_valor = 0.0
    kwh_vistos = set()  # (mes, kwh_str) - para kWh
    valores_vistos = set()  # (mes, kwh_str, valor_str) - para Valor R$
    meses = set()

    linhas = texto.split("\n")

    for linha in linhas:
        if padrao_busca not in linha.upper():
            continue

        # ═══════════════════════════════════════════════════════════
        # EXTRAIR MÊS
        # ═══════════════════════════════════════════════════════════
        m_mes = re.search(r"(\d{2}/\d{4})", linha)
        mes_str = m_mes.group(1) if m_mes else "00/0000"
        meses.add(mes_str)

        # ═══════════════════════════════════════════════════════════
        # FORMATO 1 (PRIORIZADO): GDI-I (sem kWh no meio)
        # Exemplo: ENERGIA INJ. PT OUC 02/2026 GDI-I -2291,00 0,455853 -1.044,36
        # ═══════════════════════════════════════════════════════════
        m_formato1 = re.search(r"GDI-I\s+([-\d.,]+)\s+([-\d.,]+)\s+([-\d.,]+)", linha, re.IGNORECASE)

        if m_formato1:
            try:
                kwh = abs(to_float(m_formato1.group(1)))
                tarifa = abs(to_float(m_formato1.group(2)))
                valor_rs = abs(to_float(m_formato1.group(3)))

                # Validar que os valores fazem sentido
                if kwh < 0.01 or valor_rs < 0.01:
                    continue

                # Formatar para DISTINCT
                kwh_str = f"{kwh:.2f}"
                valor_str = f"{valor_rs:.2f}"

                # DISTINCT para kWh: (mês, kWh)
                chave_kwh = (mes_str, kwh_str)
                if chave_kwh not in kwh_vistos:
                    kwh_vistos.add(chave_kwh)
                    total_kwh += kwh

                # DISTINCT para Valor R$: (mês, kWh, valor)
                chave_valor = (mes_str, kwh_str, valor_str)
                if chave_valor not in valores_vistos:
                    valores_vistos.add(chave_valor)
                    total_valor += valor_rs

                continue  # Processou formato 1, próxima linha

            except:
                pass  # Se falhou, tenta formato 2

        # ═══════════════════════════════════════════════════════════
        # FORMATO 2 (FALLBACK): GDI-kIWh ou GDI-kWh
        # Exemplo: ENERGIA INJ. PT OUC MPT TE 02/2026 GDI-kIWh -2.291 0,455853 -1.044,36 -96,60 0,00 0,413690
        # ═══════════════════════════════════════════════════════════
        m_formato2 = re.search(r"GDI-[^\s]*\s+(.*)", linha, re.IGNORECASE)

        if m_formato2:
            resto_linha = m_formato2.group(1).strip()

            # Remover "kWh" se existir no início
            resto_linha = re.sub(r"^\s*kWh\s+", "", resto_linha, flags=re.IGNORECASE)

            # Extrair todos os números
            valores_str = re.findall(r"-?[\d.,]+", resto_linha)

            if len(valores_str) < 3:
                continue

            # Filtrar valores válidos (> 0.01)
            valores_float = []
            for v in valores_str:
                try:
                    val = to_float(v)
                    if abs(val) > 0.01:
                        valores_float.append(val)
                except:
                    continue

            if len(valores_float) < 3:
                continue

            # Posição 0 = kWh, Posição 2 = Valor R$
            kwh = abs(valores_float[0])
            valor_rs = abs(valores_float[2])

            # Formatar para DISTINCT
            kwh_str = f"{kwh:.2f}"
            valor_str = f"{valor_rs:.2f}"

            # DISTINCT para kWh: (mês, kWh)
            chave_kwh = (mes_str, kwh_str)
            if chave_kwh not in kwh_vistos:
                kwh_vistos.add(chave_kwh)
                total_kwh += kwh

            # DISTINCT para Valor R$: (mês, kWh, valor)
            chave_valor = (mes_str, kwh_str, valor_str)
            if chave_valor not in valores_vistos:
                valores_vistos.add(chave_valor)
                total_valor += valor_rs

    return total_kwh, total_valor, sorted(list(meses))


def extrair_saldos_scee(texto: str) -> Dict:
    """Extrai saldos SCEE."""
    saldos = {
        "Saldo Mês Ponta": 0,
        "Saldo Mês FP": 0,
        "Saldo Acumulado Ponta": 0,
        "Saldo Acumulado FP": 0,
        "Saldo a Expirar Ponta": 0,
        "Saldo a Expirar FP": 0,
    }

    texto_limpo = texto.replace("\n", " ")

    padrao = re.compile(
        r"Demonstrativo de saldos SCEE.*?"
        r"Saldo M[eê]s Ponta\s+(\d+),\s*"
        r"Saldo M[eê]s F\s*Ponta\s+(\d+),\s*"
        r"Saldo\s+Acumulado Ponta\s+(\d+),\s*"
        r"Saldo Acumulado F\s*Ponta\s+(\d+),\s*"
        r"Saldo a Expirar Pr[oó]ximo M[eê]s Ponta\s+(\d+),\s*"
        r"Saldo a Expirar\s+Pr[oó]ximo M[eê]s F\s*Ponta\s+(\d+)",
        re.IGNORECASE
    )

    m = padrao.search(texto_limpo)
    if m:
        saldos["Saldo Mês Ponta"] = int(m.group(1))
        saldos["Saldo Mês FP"] = int(m.group(2))
        saldos["Saldo Acumulado Ponta"] = int(m.group(3))
        saldos["Saldo Acumulado FP"] = int(m.group(4))
        saldos["Saldo a Expirar Ponta"] = int(m.group(5))
        saldos["Saldo a Expirar FP"] = int(m.group(6))

    return saldos


# **4. EXTRAÇÃO - SUBSTITUIR A FUNÇÃO extrair_pagina_beneficiaria COMPLETA**

def extrair_pagina_beneficiaria(texto: str, arquivo: str) -> Optional[Dict]:
    """Extrai dados de UC beneficiária - PRIORIDADES CORRETAS."""
    if "UC benefici" not in texto and f"Geradora: UC {UC_GERADORA}" not in texto:
        if UC_GERADORA not in texto:
            return None
        if "ENERGIA INJETADA" not in texto and "ENERGIA INJ" not in texto:
            return None

    ano_emissao, mes_emissao = extrair_mes_ano_fatura(arquivo)
    ano_ref, mes_ref = calcular_mes_referencia(ano_emissao, mes_emissao)

    resultado = {
        "Tipo Registro": "COMPENSACAO",
        "Fatura": arquivo,
        "Número Fatura": None,
        "Ano Referência": ano_ref,
        "Mês Referência": mes_ref,
        "UC Geradora": UC_GERADORA,
        "UC Beneficiária": None,
        "Flag Geração Própria": 0,
        "Flag Uso Crédito": 0,
        "Grupo Tensão": None,
        "Consumo (kWh)": 0.0,
        "Energia Compensada (kWh)": 0.0,
        "Valor Compensado (R$)": 0.0,
        "Valor Fatura com Compensação (R$)": 0.0,
        "Valor Fatura sem Compensação (R$)": 0.0,
        "Saldo Crédito Anterior (R$)": 0.0,
        "Percentual Compensado (%)": 0.0,
        "Meses Compensados": [],
    }

    # ═══════════════════════════════════════════════════════════
    # CAMPOS BÁSICOS
    # ═══════════════════════════════════════════════════════════
    resultado["Número Fatura"] = extrair_numero_fatura(texto)
    resultado["Flag Uso Crédito"] = verificar_uso_credito(texto)
    resultado["UC Beneficiária"] = extrair_uc_beneficiaria(texto)

    if resultado["UC Beneficiária"] == UC_GERADORA:
        resultado["Flag Geração Própria"] = 1

    resultado["Grupo Tensão"] = extrair_grupo_tensao(texto)

    # Valor da fatura
    m = re.search(r"\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$([\d.,]+)", texto)
    if m:
        resultado["Valor Fatura com Compensação (R$)"] = to_float(m.group(1))

    resultado["Saldo Crédito Anterior (R$)"] = extrair_saldo_credito_anterior(texto)

    # Consumo kWh
    m_cons = re.search(r"\d{6,10}\s+CONSUMO kWh TP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
    if m_cons:
        resultado["Consumo (kWh)"] = to_float(m_cons.group(1))
    else:
        m_cons2 = re.search(r"ENERGIA ELETRICA CONSUMO\s+\d+\s+\d+\s+([\d.,]+)", texto)
        if m_cons2:
            resultado["Consumo (kWh)"] = to_float(m_cons2.group(1))

    # ═══════════════════════════════════════════════════════════
    # PRIORIDADE 1: ENERGIA INJ (SEMPRE PRIMEIRO - CAPTURA VALOR R$)
    # ═══════════════════════════════════════════════════════════
    kwh_comp, valor_comp, meses_comp = extrair_valores_energia_inj(texto, "ENERGIA INJ")

    if kwh_comp > 0:
        resultado["Energia Compensada (kWh)"] = kwh_comp

    if valor_comp > 0:
        resultado["Valor Compensado (R$)"] = valor_comp

    if meses_comp:
        resultado["Meses Compensados"] = meses_comp

    # ═══════════════════════════════════════════════════════════
    # PRIORIDADE 2: SALDO (SUBSTITUI APENAS kWh e Meses, PRESERVA Valor R$)
    # ═══════════════════════════════════════════════════════════
    saldo_cred_kwh, saldo_cred_rs, saldo_dev_kwh, saldo_dev_rs = extrair_saldo_creditos_devolver(texto)

    if saldo_cred_kwh > 0 and saldo_dev_kwh > 0:
        kwh_compensado_saldo = saldo_cred_kwh - saldo_dev_kwh

        # SUBSTITUI kWh
        resultado["Energia Compensada (kWh)"] = kwh_compensado_saldo

        # SUBSTITUI Meses
        resultado["Meses Compensados"] = [f"{mes_ref:02d}/{ano_ref}"]

        # PRESERVA Valor R$ da PRIORIDADE 1 (ENERGIA INJ)
        # Só usa o saldo se não tem valor da ENERGIA INJ
        if resultado["Valor Compensado (R$)"] == 0.0:
            valor_compensado_saldo = saldo_cred_rs - saldo_dev_rs
            resultado["Valor Compensado (R$)"] = valor_compensado_saldo

    # ═══════════════════════════════════════════════════════════
    # PRIORIDADE 3: TEXTO ESPECIAL (FALLBACK - SÓ SE kWh = 0)
    # ═══════════════════════════════════════════════════════════
    if resultado["Energia Compensada (kWh)"] == 0.0:
        kwh_pt_texto, kwh_fp_texto = extrair_consumo_compensado_texto(texto)

        if kwh_pt_texto > 0 or kwh_fp_texto > 0:
            resultado["Energia Compensada (kWh)"] = kwh_pt_texto + kwh_fp_texto
            resultado["Meses Compensados"] = [f"{mes_ref:02d}/{ano_ref}"]

    # ═══════════════════════════════════════════════════════════
    # FINALIZAÇÃO
    # ═══════════════════════════════════════════════════════════
    # Calcular Valor sem Compensação
    resultado["Valor Fatura sem Compensação (R$)"] = (
        resultado["Valor Compensado (R$)"] + resultado["Valor Fatura com Compensação (R$)"]
    )

    # Saldos SCEE
    saldos = extrair_saldos_scee(texto)
    resultado.update(saldos)

    # Percentual
    if resultado["Consumo (kWh)"] > 0:
        resultado["Percentual Compensado (%)"] = round(
            (resultado["Energia Compensada (kWh)"] / resultado["Consumo (kWh)"]) * 100,
            1
        )

    return resultado


# **EXTRAÇÃO - USINA GERADORA**

def extrair_pagina_geradora(textos_paginas: List[str], arquivo: str) -> Optional[Dict]:
    """Extrai dados da usina geradora."""
    ano_emissao, mes_emissao = extrair_mes_ano_fatura(arquivo)
    ano_ref, mes_ref = calcular_mes_referencia(ano_emissao, mes_emissao)

    geracao = {
        "Tipo Registro": "GERACAO/COMPENSACAO",
        "Fatura": arquivo,
        "Número Fatura": None,  # ← NOVO
        "Ano Referência": ano_ref,
        "Mês Referência": mes_ref,
        "UC Geradora": UC_GERADORA,
        "UC Beneficiária": UC_GERADORA,
        "Flag Geração Própria": 1,
        "Flag Uso Crédito": 0,  # ← NOVO
        "Grupo Tensão": None,
        "Consumo (kWh)": 0.0,
        "Energia Gerada (kWh)": 0.0,
        "Energia Compensada (kWh)": 0.0,
        "Valor Compensado (R$)": 0.0,
        "Valor Fatura com Compensação (R$)": 0.0,
        "Valor Fatura sem Compensação (R$)": 0.0,
        "Saldo Crédito Anterior (R$)": 0.0,
        "Percentual Compensado (%)": 0.0,
        "Meses Compensados": [],
    }

    kwh_gerado_pt = 0.0
    kwh_gerado_fp = 0.0

    for texto in textos_paginas:
        # Filtrar APENAS páginas da usina
        if UC_GERADORA not in texto:
            continue

        if "GERAC kWh" not in texto and "ENERGIA GERADA/INJETADA" not in texto:
            continue

        # ═══════════════════════════════════════════════════════════
        # NOVOS CAMPOS
        # ═══════════════════════════════════════════════════════════
        if not geracao["Número Fatura"]:
            geracao["Número Fatura"] = extrair_numero_fatura(texto)

        if geracao["Flag Uso Crédito"] == 0:
            geracao["Flag Uso Crédito"] = verificar_uso_credito(texto)

        # Grupo de Tensão
        if not geracao["Grupo Tensão"]:
            geracao["Grupo Tensão"] = extrair_grupo_tensao(texto)

        # Valor da fatura
        if not geracao["Valor Fatura com Compensação (R$)"]:
            m = re.search(
                rf"{UC_GERADORA}\s+\d{{2}}/\d{{4}}\s+\d{{2}}/\d{{2}}/\d{{4}}\s+R\$([\d.,]+)",
                texto
            )
            if not m:
                m = re.search(r"\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$([\d.,]+)", texto)
            if m:
                geracao["Valor Fatura com Compensação (R$)"] = to_float(m.group(1))

        # Saldo Crédito Anterior
        if geracao["Saldo Crédito Anterior (R$)"] == 0.0:
            geracao["Saldo Crédito Anterior (R$)"] = extrair_saldo_credito_anterior(texto)

        # Consumo da usina
        if geracao["Consumo (kWh)"] == 0.0:
            m_cons = re.search(r"\d{7,10}\s+CONSUMO kWh TP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
            if m_cons:
                geracao["Consumo (kWh)"] = to_float(m_cons.group(1))

        # Geração (GERAC)
        m_pt = re.search(r"\d{7,10}\s+GERAC kWh PT\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
        if m_pt:
            kwh_gerado_pt = to_float(m_pt.group(1))

        m_fp = re.search(r"\d{7,10}\s+GERAC kWh FP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
        if m_fp:
            kwh_gerado_fp = to_float(m_fp.group(1))

        # Geração alternativa (EXTRATO)
        m_ext_pt = re.search(r"ENERGIA GERADA/INJETADA PONTA\s+\d+\s+\d+\s+([\d.,]+)", texto)
        if m_ext_pt and kwh_gerado_pt == 0:
            kwh_gerado_pt = to_float(m_ext_pt.group(1))

        m_ext_fp = re.search(r"ENERGIA GERADA/INJETADA FORA P\s+\d+\s+\d+\s+([\d.,]+)", texto)
        if m_ext_fp and kwh_gerado_fp == 0:
            kwh_gerado_fp = to_float(m_ext_fp.group(1))

        # Compensação na usina
        if "ENERGIA INJETADA" in texto.upper():
            kwh_comp, valor_comp, meses_comp = extrair_valores_energia_inj(texto, "ENERGIA INJETADA")

            if kwh_comp > 0:
                geracao["Energia Compensada (kWh)"] = kwh_comp

            if valor_comp > 0:
                geracao["Valor Compensado (R$)"] = valor_comp

            if meses_comp:
                geracao["Meses Compensados"] = meses_comp

        # Saldos SCEE
        saldos = extrair_saldos_scee(texto)
        if saldos["Saldo Mês Ponta"] > 0 or saldos["Saldo Mês FP"] > 0:
            geracao.update(saldos)

    # Finalizar
    geracao["Energia Gerada (kWh)"] = kwh_gerado_pt + kwh_gerado_fp

    # Calcular Valor sem Compensação
    geracao["Valor Fatura sem Compensação (R$)"] = (
        geracao["Valor Compensado (R$)"] + geracao["Valor Fatura com Compensação (R$)"]
    )

    # Percentual
    if geracao["Energia Gerada (kWh)"] > 0:
        geracao["Percentual Compensado (%)"] = round(
            (geracao["Energia Compensada (kWh)"] / geracao["Energia Gerada (kWh)"]) * 100,
            1
        )

    if geracao["Energia Gerada (kWh)"] == 0 and geracao["Valor Fatura com Compensação (R$)"] == 0:
        return None

    return geracao



# **PROCESSAMENTO**

def processar_pdf(caminho_pdf: str) -> Dict:
    """Processa um PDF completo - AGRUPAMENTO MELHORADO"""
    arquivo = os.path.basename(caminho_pdf)

    # ═══════════════════════════════════════════════════════════
    # ETAPA 1: AGRUPAR PÁGINAS POR UC (MELHORADO)
    # ═══════════════════════════════════════════════════════════
    paginas_por_uc = {}
    todos_textos = []
    uc_anterior = None

    with pdfplumber.open(caminho_pdf) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            todos_textos.append(texto)

            uc = extrair_uc_beneficiaria(texto)

            # Se não encontrou UC nesta página, assume que é continuação da anterior
            if not uc and uc_anterior:
                uc = uc_anterior

            if uc:
                if uc not in paginas_por_uc:
                    paginas_por_uc[uc] = []
                paginas_por_uc[uc].append(texto)
                uc_anterior = uc  # Guarda para próxima página

    # ═══════════════════════════════════════════════════════════
    # ETAPA 2: PROCESSAR BENEFICIÁRIAS
    # ═══════════════════════════════════════════════════════════
    beneficiarias = []

    for uc, textos_uc in paginas_por_uc.items():
        if uc == UC_GERADORA:
            continue

        grupo = extrair_grupo_tensao(textos_uc[0])
        texto_completo = "\n".join(textos_uc)

        # DEBUG ANTES de processar
        if grupo == "A4":
            kwh_debug, valor_debug, meses_debug = extrair_valores_energia_inj(texto_completo, "ENERGIA INJ")
            print(f"  🔍 ANTES → UC {uc}: kwh={kwh_debug:.2f}, valor={valor_debug:.2f}, páginas={len(textos_uc)}")

        dados_bene = extrair_pagina_beneficiaria(texto_completo, arquivo)

        if dados_bene and dados_bene["UC Beneficiária"]:
            # Buscar número da fatura em TODAS as páginas
            if not dados_bene["Número Fatura"]:
                for idx, texto_pagina in enumerate(textos_uc, start=1):
                    numero_fatura = extrair_numero_fatura(texto_pagina)
                    if numero_fatura:
                        dados_bene["Número Fatura"] = numero_fatura
                        if grupo == "A4":
                            print(f"    ✅ Fatura encontrada na página {idx}: {numero_fatura}")
                        break

                # DEBUG: Se ainda não achou e é A4, avisar
                if not dados_bene["Número Fatura"] and grupo == "A4":
                    print(f"    ⚠️  UC {uc}: Número da fatura NÃO encontrado em {len(textos_uc)} página(s)")

            # Debug para A4
            if grupo == "A4":
                print(f"  ✅ DEPOIS → UC {uc}: kWh={dados_bene['Energia Compensada (kWh)']}, R$={dados_bene['Valor Compensado (R$)']:.2f}, Fatura={dados_bene['Número Fatura']}")

            beneficiarias.append(dados_bene)

    # ═══════════════════════════════════════════════════════════
    # ETAPA 3: PROCESSAR GERADORA
    # ═══════════════════════════════════════════════════════════
    dados_geradora = extrair_pagina_geradora(todos_textos, arquivo)

    return {
        "beneficiarias": beneficiarias,
        "geradora": dados_geradora
    }

"""# **5. CONSOLIDAÇÃO | Agrupa tudo em DataFrames**"""

def consolidar_dados(pasta_pdfs: Optional[str] = None,
                    teste_mes_ano: Optional[Tuple[int, int]] = None) -> pd.DataFrame:
    """Consolida todos os dados extraídos."""
    pasta = pasta_pdfs or BASE_LIMPOS
    registros = []

    arquivos = sorted([
        (f, os.path.join(r, f))
        for r, _, files in os.walk(pasta)
        for f in files
        if f.lower().endswith(".pdf")
    ], key=lambda x: extrair_mes_ano_fatura(x[0]))

    if teste_mes_ano:
        ano_teste, mes_teste = teste_mes_ano
        arquivos = [
            (f, c) for f, c in arquivos
            if extrair_mes_ano_fatura(f) == (ano_teste, mes_teste)
        ]
        print(f"\n🧪 MODO TESTE: Apenas {ano_teste}/{mes_teste:02d}")

    print(f"\n📊 Processando {len(arquivos)} arquivo(s)...")

    for nome, caminho in arquivos:
        print(f"  📄 {nome} ... ", end="")

        resultado = processar_pdf(caminho)
        n_bene = len(resultado["beneficiarias"])
        tem_ger = resultado["geradora"] is not None

        registros.extend(resultado["beneficiarias"])

        if resultado["geradora"]:
            registros.append(resultado["geradora"])

        print(f"{n_bene} beneficiárias | geradora: {'✅' if tem_ger else '—'}")

    df = pd.DataFrame(registros)

    if not df.empty:
        # Formatação de colunas
        colunas_inteiras = [
            "Consumo (kWh)",
            "Energia Compensada (kWh)",
            "Energia Gerada (kWh)",
            "Saldo Mês Ponta",
            "Saldo Mês FP",
            "Saldo Acumulado Ponta",
            "Saldo Acumulado FP",
            "Saldo a Expirar Ponta",
            "Saldo a Expirar FP"
        ]
        for col in colunas_inteiras:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        colunas_float = [
            "Valor Compensado (R$)",
            "Valor Fatura com Compensação (R$)",
            "Valor Fatura sem Compensação (R$)",
            "Saldo Crédito Anterior (R$)"
        ]
        for col in colunas_float:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).round(2)

        if "Percentual Compensado (%)" in df.columns:
            df["Percentual Compensado (%)"] = pd.to_numeric(
                df["Percentual Compensado (%)"],
                errors="coerce"
            ).fillna(0.0).round(1)

        if "Meses Compensados" in df.columns:
            df["Meses Compensados (Texto)"] = df["Meses Compensados"].apply(
                lambda x: " | ".join(x) if isinstance(x, list) and x else ""
            )

    return df

"""# **6. RELATÓRIOS | Gera resumos e totalizadores**"""

def gerar_resumo_mensal(df: pd.DataFrame) -> pd.DataFrame:
    """Gera resumo mensal."""
    if df.empty:
        return pd.DataFrame()

    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)].copy()
    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"].copy()

    if not df_ger.empty:
        resumo_ger = df_ger.groupby(["Ano Referência", "Mês Referência"]).agg({
            "Energia Gerada (kWh)": "sum",
            "Energia Compensada (kWh)": "sum",
        }).reset_index()
        resumo_ger.rename(columns={
            "Energia Compensada (kWh)": "kWh Compensado na Usina"
        }, inplace=True)
    else:
        resumo_ger = pd.DataFrame(columns=["Ano Referência", "Mês Referência"])

    if not df_comp.empty:
        resumo_comp = df_comp.groupby(["Ano Referência", "Mês Referência"]).agg(
            total_ucs_compensadas=("UC Beneficiária", "nunique"),
            kwh_compensado_total=("Energia Compensada (kWh)", "sum"),
            valor_compensado_total=("Valor Compensado (R$)", "sum"),
            valor_faturas_total=("Valor Fatura com Compensação (R$)", "sum"),
        ).reset_index()
    else:
        resumo_comp = pd.DataFrame(columns=["Ano Referência", "Mês Referência"])

    if not resumo_ger.empty and not resumo_comp.empty:
        resumo = resumo_ger.merge(
            resumo_comp,
            on=["Ano Referência", "Mês Referência"],
            how="outer"
        )
    elif not resumo_ger.empty:
        resumo = resumo_ger
    else:
        resumo = resumo_comp

    return resumo.sort_values(["Ano Referência", "Mês Referência"])

def salvar_outputs(df: pd.DataFrame, sufixo: str = ""):
    """Salva DataFrames em CSVs."""
    if df.empty:
        print("⚠️  DataFrame vazio")
        return

    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)].copy()
    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"].copy()

    df_save = df.copy()
    if "Meses Compensados" in df_save.columns:
        df_save = df_save.drop(columns=["Meses Compensados"])

    p_ger = os.path.join(BASE_OUTPUT, f"geracao{sufixo}.csv")
    p_comp = os.path.join(BASE_OUTPUT, f"compensacao{sufixo}.csv")
    p_resumo = os.path.join(BASE_OUTPUT, f"resumo_mensal{sufixo}.csv")
    p_completo = os.path.join(BASE_OUTPUT, f"base_completa{sufixo}.csv")

    if not df_ger.empty:
        df_ger_save = df_ger.copy()
        if "Meses Compensados" in df_ger_save.columns:
            df_ger_save = df_ger_save.drop(columns=["Meses Compensados"])
        df_ger_save.to_csv(p_ger, index=False, encoding="utf-8-sig")
        print(f"💾 Geração: {p_ger}")

    if not df_comp.empty:
        df_comp_save = df_comp.copy()
        if "Meses Compensados" in df_comp_save.columns:
            df_comp_save = df_comp_save.drop(columns=["Meses Compensados"])
        df_comp_save.to_csv(p_comp, index=False, encoding="utf-8-sig")
        print(f"💾 Compensação: {p_comp}")

    df_resumo = gerar_resumo_mensal(df)
    if not df_resumo.empty:
        df_resumo.to_csv(p_resumo, index=False, encoding="utf-8-sig")
        print(f"💾 Resumo mensal: {p_resumo}")

    df_save.to_csv(p_completo, index=False, encoding="utf-8-sig")
    print(f"💾 Base completa: {p_completo}")

def imprimir_resumo(df: pd.DataFrame):
    """Imprime resumo dos dados."""
    if df.empty:
        print("\n⚠️  Nenhum dado extraído")
        return

    print("\n" + "="*60)
    print("📊 RESUMO DOS DADOS EXTRAÍDOS")
    print("="*60)

    print("\n📌 POR TIPO DE REGISTRO:")
    print(df["Tipo Registro"].value_counts().to_string())

    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)]
    if not df_ger.empty:
        print("\n⚡ GERAÇÃO (USINA):")
        total_ger = df_ger["Energia Gerada (kWh)"].sum()
        total_comp_usina = df_ger["Energia Compensada (kWh)"].sum()
        print(f"  Total gerado: {total_ger:,.0f} kWh")
        print(f"  Compensado na usina: {total_comp_usina:,.0f} kWh")

    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"]
    if not df_comp.empty:
        print("\n🔋 COMPENSAÇÃO (UCs):")
        n_ucs = df_comp["UC Beneficiária"].nunique()
        total_comp = df_comp["Energia Compensada (kWh)"].sum()
        total_val = df_comp["Valor Compensado (R$)"].sum()
        total_fat = df_comp["Valor Fatura com Compensação (R$)"].sum()
        print(f"  UCs beneficiadas: {n_ucs}")
        print(f"  Total compensado: {total_comp:,.0f} kWh")
        print(f"  Valor compensado: R$ {total_val:,.2f}")
        print(f"  Total faturas: R$ {total_fat:,.2f}")

    if "Saldo Acumulado FP" in df.columns:
        saldo_fp = df["Saldo Acumulado FP"].max()
        saldo_pt = df["Saldo Acumulado Ponta"].max()
        print(f"\n💰 SALDOS MÁXIMOS:")
        print(f"  Ponta: {saldo_pt:,} kWh")
        print(f"  Fora Ponta: {saldo_fp:,} kWh")

    print("="*60)

"""# **7. EXECUÇÃO DO PIPELINE**"""

def executar_pipeline(debug: bool = True,
                     salvar: bool = True,
                     modo_teste: bool = False,
                     mes_teste: int = 3,
                     ano_teste: int = 2026):
    """Executa o pipeline completo."""
    print("\n" + "="*60)
    print("🚀 INICIANDO PIPELINE COPEL v4.2 - EXTRAÇÃO CORRIGIDA")
    print("="*60)

    teste_mes_ano = (ano_teste, mes_teste) if modo_teste else None

    print("\n📄 ETAPA 1: Limpeza dos PDFs")
    processar_limpeza(teste_mes_ano=teste_mes_ano)

    print("\n📊 ETAPA 2: Extração e Consolidação")
    df = consolidar_dados(teste_mes_ano=teste_mes_ano)

    if debug:
        imprimir_resumo(df)

        if not df.empty:
            print("\n🔎 AMOSTRA DOS DADOS (primeiras 10 linhas):")
            print(df.head(10).to_string())

    if salvar and not df.empty:
        sufixo = f"_{ano_teste}-{mes_teste:02d}" if modo_teste else ""
        print(f"\n💾 ETAPA 3: Salvando outputs{sufixo}...")
        salvar_outputs(df, sufixo=sufixo)

    print("\n" + "="*60)
    print("✅ PIPELINE FINALIZADO")
    print("="*60)

    return df

"""# **8. MAIN**"""

if __name__ == "__main__":
    df_resultado = executar_pipeline(
        debug=True,
        salvar=True,
        modo_teste=False,
        mes_teste=3,
        ano_teste=2026
    )
