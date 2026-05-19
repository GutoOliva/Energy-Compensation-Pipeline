#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════
COPEL ENERGY COMPENSATION PIPELINE - PRODUCTION VERSION
═══════════════════════════════════════════════════════════════════════════
Author: Energy Analytics Team
Version: 5.0.0
Environment: Google Colab & GitHub Actions
Description: Automated pipeline for COPEL energy invoice processing
═══════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from calendar import monthrange

import pandas as pd
import pdfplumber
from pypdf import PdfReader, PdfWriter

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: ENVIRONMENT SETUP & CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

class Config:
    """Global configuration management"""
    
    # Core Settings
    UC_GERADORA = "109441346"
    GOOGLE_DRIVE_FOLDER_ID = "1VXPSxSo7r-j1mF0H31c159V4aMn1iGN2"
    
    # Environment Detection
    @staticmethod
    def detect_environment() -> str:
        """Detect execution environment"""
        if 'COLAB_GPU' in os.environ or 'google.colab' in sys.modules:
            return 'colab'
        elif 'GITHUB_ACTIONS' in os.environ:
            return 'github_actions'
        return 'local'
    
    # Path Configuration
    @staticmethod
    def setup_paths(env: str) -> Dict[str, Path]:
        """Configure paths based on environment"""
        if env == 'colab':
            base = Path('/content/drive/MyDrive/Dados')
        elif env == 'github_actions':
            workspace = os.getenv('GITHUB_WORKSPACE', '/tmp/pipeline')
            base = Path(workspace) / 'data'
        else:
            base = Path('./data')
        
        paths = {
            'origem': base / '01-Brutos',
            'limpos': base / '02-Limpos',
            'output': base / '03-Output'
        }
        
        # Create directories
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        
        return paths

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

def setup_logging(env: str) -> logging.Logger:
    """Configure professional logging"""
    log_format = '%(asctime)s [%(levelname)s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger = logging.getLogger('COPEL_Pipeline')
    logger.info(f"Environment: {env.upper()}")
    return logger

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: GOOGLE DRIVE INTEGRATION (GitHub Actions Only)
# ═══════════════════════════════════════════════════════════════════════════

class GoogleDriveManager:
    """Handle Google Drive operations"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.service = None
        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize Google Drive API service"""
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
            
            # Load credentials from environment
            creds_json = os.getenv('GOOGLE_CREDENTIALS')
            if not creds_json:
                raise ValueError("GOOGLE_CREDENTIALS not found in environment")
            
            creds_dict = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(
                creds_dict,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            self.service = build('drive', 'v3', credentials=credentials)
            self.logger.info("✅ Google Drive connection established")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Google Drive: {e}")
            raise
    
    def download_pdfs(self, folder_id: str, destination: Path) -> int:
        """Download PDFs from Google Drive folder"""
        try:
            from googleapiclient.http import MediaIoBaseDownload
            import io
            
            self.logger.info(f"📥 Downloading PDFs from folder: {folder_id}")
            
            query = f"'{folder_id}' in parents and mimeType='application/pdf'"
            results = self.service.files().list(
                q=query,
                fields='files(id, name, modifiedTime)'
            ).execute()
            
            files = results.get('files', [])
            self.logger.info(f"Found {len(files)} PDF files")
            
            downloaded = 0
            for file in files:
                file_id = file['id']
                file_name = file['name']
                dest_path = destination / file_name
                
                if dest_path.exists():
                    self.logger.info(f"  ⏭️  {file_name} (already exists)")
                    continue
                
                # Download file
                request = self.service.files().get_media(fileId=file_id)
                with dest_path.open('wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                        if status:
                            progress = int(status.progress() * 100)
                            self.logger.info(f"  📥 {file_name}: {progress}%")
                
                downloaded += 1
                self.logger.info(f"  ✅ {file_name}")
            
            self.logger.info(f"✅ Downloaded {downloaded} new files")
            return downloaded
            
        except Exception as e:
            self.logger.error(f"❌ Error downloading PDFs: {e}")
            raise
    
    def upload_results(self, folder_id: str, source_dir: Path) -> int:
        """Upload result CSVs to Google Drive"""
        try:
            from googleapiclient.http import MediaFileUpload
            
            self.logger.info("📤 Uploading results to Google Drive")
            
            csv_files = list(source_dir.glob('*.csv'))
            uploaded = 0
            
            for csv_path in csv_files:
                file_name = csv_path.name
                
                # Check if file already exists
                query = f"name='{file_name}' and '{folder_id}' in parents"
                results = self.service.files().list(q=query).execute()
                existing = results.get('files', [])
                
                file_metadata = {'name': file_name, 'parents': [folder_id]}
                media = MediaFileUpload(str(csv_path), mimetype='text/csv')
                
                if existing:
                    # Update existing file
                    file_id = existing[0]['id']
                    self.service.files().update(
                        fileId=file_id,
                        media_body=media
                    ).execute()
                    self.logger.info(f"  🔄 {file_name} (updated)")
                else:
                    # Create new file
                    self.service.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields='id'
                    ).execute()
                    self.logger.info(f"  ✅ {file_name} (created)")
                
                uploaded += 1
            
            self.logger.info(f"✅ Uploaded {uploaded} files")
            return uploaded
            
        except Exception as e:
            self.logger.error(f"❌ Error uploading results: {e}")
            raise

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def to_float(s: str) -> float:
    """Convert Brazilian number format to float"""
    if s is None or s == "":
        return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def extrair_mes_ano_fatura(nome_arquivo: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract year and month from filename (COPEL-MM-YYYY.pdf)"""
    m = re.search(r"COPEL-(\d{2})-(\d{4})", nome_arquivo)
    if m:
        return int(m.group(2)), int(m.group(1))  # (year, month)
    return None, None

def calcular_mes_referencia(ano_emissao: int, mes_emissao: int) -> Tuple[int, int]:
    """Calculate reference month (previous month)"""
    if mes_emissao == 1:
        return ano_emissao - 1, 12
    return ano_emissao, mes_emissao - 1

def arquivo_valido(nome: str) -> bool:
    """Check if file is valid (after Sep/2024)"""
    ano, mes = extrair_mes_ano_fatura(nome)
    if ano is None:
        return False
    return (ano > 2024) or (ano == 2024 and mes >= 9)

def tem_energia_injetada(texto: str) -> bool:
    """Check if page contains energy injection data"""
    t = texto.lower()
    patterns = [
        r"energia\s+injetada",
        r"energia\s+inj",
        r"injetada",
        r"inj\s*kwh",
        r"energia\s+compensada"
    ]
    return any(re.search(p, t) for p in patterns)

def contem_uc_geradora(texto: str) -> bool:
    """Check if page contains generating UC"""
    return Config.UC_GERADORA in texto

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: EXTRACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def extrair_numero_fatura(texto: str) -> Optional[str]:
    """Extract invoice number"""
    patterns = [
        r"FAT-\d+-(\d+)-\d+",  # FAT-01-XXXXXXXXXXXXX-XX
        r"FAT-\d+-(\d+)\.\d+",  # FAT-01-XXXXXXXXXXXXX.XX
        r"FAT[-.]?\d+[-.](\d{14})",  # FAT with 14 digits
        r"(?:NOTA FISCAL|NFe|NF-e|Nº|N°|FATURA)[:\s]*[N°]*[:\s]*(\d{14})",
        r"(?:Número|Numero)[:\s]+(\d{14})",
        r"\b(202[4-9]\d{10})\b"  # 14 digits starting with 202X
    ]
    
    for pattern in patterns:
        m = re.search(pattern, texto, re.IGNORECASE)
        if m:
            return m.group(1)
    return None

def verificar_uso_credito(texto: str) -> int:
    """Check if UC used credits"""
    return 1 if "SALDO DE CREDITOS" in texto.upper() else 0

def extrair_uc_beneficiaria(texto: str) -> Optional[str]:
    """Extract beneficiary UC number"""
    linhas = texto.split("\n")
    
    # Priority 1: After "UNIDADE CONSUMIDORA"
    for i, linha in enumerate(linhas):
        if "UNIDADE CONSUMIDORA" in linha.upper():
            for j in range(i, min(i+4, len(linhas))):
                nums = re.findall(r"^\s*(\d{7,9})\s*$", linhas[j])
                if nums:
                    return nums[0]
    
    # Priority 2: In address line before "NOTA FISCAL"
    for linha in linhas:
        if linha.startswith("Endereço:") or "Endere" in linha:
            parte_antes_nota = linha.split("NOTA FISCAL")[0]
            nums = re.findall(r"\b(\d{7,9})\b", parte_antes_nota)
            if nums:
                return nums[-1]
    
    # Priority 3: First 10 lines
    for linha in linhas[:10]:
        nums = re.findall(r"^\s*(\d{8,9})\s*$", linha)
        if nums:
            return nums[0]
    
    return None

def extrair_grupo_tensao(texto: str) -> Optional[str]:
    """Extract tension group (A3A, A4, B3, etc)"""
    linhas = texto.split("\n")
    keywords = ["Poder Publico", "Comercial", "Industrial", "Rural",
                "Residencial", "Servico Publico", "Ppe-Justica"]
    
    for linha in linhas:
        if any(kw.lower() in linha.lower() for kw in keywords):
            m = re.search(r"\b([AB]\d+[a-z]*)\b", linha, re.IGNORECASE)
            if m:
                return m.group(1).upper()
    
    # Try classification field
    m = re.search(r"Classifica[çc][ãa]o:\s*([AB]\d+[a-z]*)", texto, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    
    return None

def extrair_valores_energia_inj(texto: str, padrao_busca: str = "ENERGIA INJ") -> Tuple[float, float, List[str]]:
    """
    Extract kWh and R$ values from ENERGIA INJ lines
    Supports two formats with DISTINCT logic
    """
    total_kwh = 0.0
    total_valor = 0.0
    kwh_vistos = set()
    valores_vistos = set()
    meses = set()
    
    for linha in texto.split("\n"):
        if padrao_busca not in linha.upper():
            continue
        
        # Extract month
        m_mes = re.search(r"(\d{2}/\d{4})", linha)
        mes_str = m_mes.group(1) if m_mes else "00/0000"
        meses.add(mes_str)
        
        # Format 1: GDI-I (clean)
        m1 = re.search(r"GDI-I\s+([-\d.,]+)\s+([-\d.,]+)\s+([-\d.,]+)", linha, re.IGNORECASE)
        if m1:
            try:
                kwh = abs(to_float(m1.group(1)))
                valor_rs = abs(to_float(m1.group(3)))
                
                if kwh >= 0.01 and valor_rs >= 0.01:
                    kwh_str = f"{kwh:.2f}"
                    valor_str = f"{valor_rs:.2f}"
                    
                    chave_kwh = (mes_str, kwh_str)
                    if chave_kwh not in kwh_vistos:
                        kwh_vistos.add(chave_kwh)
                        total_kwh += kwh
                    
                    chave_valor = (mes_str, kwh_str, valor_str)
                    if chave_valor not in valores_vistos:
                        valores_vistos.add(chave_valor)
                        total_valor += valor_rs
                continue
            except:
                pass
        
        # Format 2: GDI-kIWh (fallback)
        m2 = re.search(r"GDI-[^\s]*\s+(.*)", linha, re.IGNORECASE)
        if m2:
            resto = re.sub(r"^\s*kWh\s+", "", m2.group(1), flags=re.IGNORECASE)
            valores_str = re.findall(r"-?[\d.,]+", resto)
            
            if len(valores_str) >= 3:
                valores_float = [to_float(v) for v in valores_str if abs(to_float(v)) > 0.01]
                if len(valores_float) >= 3:
                    kwh = abs(valores_float[0])
                    valor_rs = abs(valores_float[2])
                    
                    kwh_str = f"{kwh:.2f}"
                    valor_str = f"{valor_rs:.2f}"
                    
                    chave_kwh = (mes_str, kwh_str)
                    if chave_kwh not in kwh_vistos:
                        kwh_vistos.add(chave_kwh)
                        total_kwh += kwh
                    
                    chave_valor = (mes_str, kwh_str, valor_str)
                    if chave_valor not in valores_vistos:
                        valores_vistos.add(chave_valor)
                        total_valor += valor_rs
    
    return total_kwh, total_valor, sorted(list(meses))

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: DATA EXTRACTION - BENEFICIARY UCS
# ═══════════════════════════════════════════════════════════════════════════

def extrair_saldo_credito_anterior(texto: str) -> float:
    """Extract previous credit balance"""
    m = re.search(r"SALDO DE CREDITOS FATURA ANTERIOR.*?(-?[\d.,]+)", texto)
    return to_float(m.group(1)) if m else 0.0

def extrair_saldo_creditos_devolver(texto: str) -> Tuple[float, float, float, float]:
    """Extract credit and return balances"""
    saldo_cred_kwh = saldo_cred_rs = saldo_dev_kwh = saldo_dev_rs = 0.0
    
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
    """Extract compensated consumption from special text"""
    pattern = re.compile(
        r"CONSUMO COMPENSADO.*?\(PONTA\s+([\d.,]+)\s+kWh,\s+F\s*PONTA\s+([\d.,]+)\s+kWh",
        re.IGNORECASE | re.DOTALL
    )
    m = pattern.search(texto)
    if m:
        return to_float(m.group(1)), to_float(m.group(2))
    return 0.0, 0.0

def extrair_saldos_scee(texto: str) -> Dict:
    """Extract SCEE balance data"""
    saldos = {
        "Saldo Mês Ponta": 0,
        "Saldo Mês FP": 0,
        "Saldo Acumulado Ponta": 0,
        "Saldo Acumulado FP": 0,
        "Saldo a Expirar Ponta": 0,
        "Saldo a Expirar FP": 0,
    }
    
    pattern = re.compile(
        r"Demonstrativo de saldos SCEE.*?"
        r"Saldo M[eê]s Ponta\s+(\d+),\s*"
        r"Saldo M[eê]s F\s*Ponta\s+(\d+),\s*"
        r"Saldo\s+Acumulado Ponta\s+(\d+),\s*"
        r"Saldo Acumulado F\s*Ponta\s+(\d+),\s*"
        r"Saldo a Expirar Pr[oó]ximo M[eê]s Ponta\s+(\d+),\s*"
        r"Saldo a Expirar\s+Pr[oó]ximo M[eê]s F\s*Ponta\s+(\d+)",
        re.IGNORECASE
    )
    
    m = pattern.search(texto.replace("\n", " "))
    if m:
        saldos["Saldo Mês Ponta"] = int(m.group(1))
        saldos["Saldo Mês FP"] = int(m.group(2))
        saldos["Saldo Acumulado Ponta"] = int(m.group(3))
        saldos["Saldo Acumulado FP"] = int(m.group(4))
        saldos["Saldo a Expirar Ponta"] = int(m.group(5))
        saldos["Saldo a Expirar FP"] = int(m.group(6))
    
    return saldos

def extrair_pagina_beneficiaria(texto: str, arquivo: str) -> Optional[Dict]:
    """Extract beneficiary UC data with correct priorities"""
    if "UC benefici" not in texto and f"Geradora: UC {Config.UC_GERADORA}" not in texto:
        if Config.UC_GERADORA not in texto:
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
        "UC Geradora": Config.UC_GERADORA,
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
    
    # Basic fields
    resultado["Número Fatura"] = extrair_numero_fatura(texto)
    resultado["Flag Uso Crédito"] = verificar_uso_credito(texto)
    resultado["UC Beneficiária"] = extrair_uc_beneficiaria(texto)
    
    if resultado["UC Beneficiária"] == Config.UC_GERADORA:
        resultado["Flag Geração Própria"] = 1
    
    resultado["Grupo Tensão"] = extrair_grupo_tensao(texto)
    
    # Invoice value
    m = re.search(r"\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$([\d.,]+)", texto)
    if m:
        resultado["Valor Fatura com Compensação (R$)"] = to_float(m.group(1))
    
    resultado["Saldo Crédito Anterior (R$)"] = extrair_saldo_credito_anterior(texto)
    
    # Consumption
    m_cons = re.search(r"\d{6,10}\s+CONSUMO kWh TP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
    if m_cons:
        resultado["Consumo (kWh)"] = to_float(m_cons.group(1))
    else:
        m_cons2 = re.search(r"ENERGIA ELETRICA CONSUMO\s+\d+\s+\d+\s+([\d.,]+)", texto)
        if m_cons2:
            resultado["Consumo (kWh)"] = to_float(m_cons2.group(1))
    
    # PRIORITY 1: ENERGIA INJ (captures R$ value)
    kwh_comp, valor_comp, meses_comp = extrair_valores_energia_inj(texto, "ENERGIA INJ")
    if kwh_comp > 0:
        resultado["Energia Compensada (kWh)"] = kwh_comp
    if valor_comp > 0:
        resultado["Valor Compensado (R$)"] = valor_comp
    if meses_comp:
        resultado["Meses Compensados"] = meses_comp
    
    # PRIORITY 2: BALANCE (replaces kWh only, preserves R$)
    saldo_cred_kwh, saldo_cred_rs, saldo_dev_kwh, saldo_dev_rs = extrair_saldo_creditos_devolver(texto)
    if saldo_cred_kwh > 0 and saldo_dev_kwh > 0:
        kwh_compensado_saldo = saldo_cred_kwh - saldo_dev_kwh
        resultado["Energia Compensada (kWh)"] = kwh_compensado_saldo
        resultado["Meses Compensados"] = [f"{mes_ref:02d}/{ano_ref}"]
        
        if resultado["Valor Compensado (R$)"] == 0.0:
            resultado["Valor Compensado (R$)"] = saldo_cred_rs - saldo_dev_rs
    
    # PRIORITY 3: SPECIAL TEXT (fallback if kWh = 0)
    if resultado["Energia Compensada (kWh)"] == 0.0:
        kwh_pt, kwh_fp = extrair_consumo_compensado_texto(texto)
        if kwh_pt > 0 or kwh_fp > 0:
            resultado["Energia Compensada (kWh)"] = kwh_pt + kwh_fp
            resultado["Meses Compensados"] = [f"{mes_ref:02d}/{ano_ref}"]
    
    # Finalization
    resultado["Valor Fatura sem Compensação (R$)"] = (
        resultado["Valor Compensado (R$)"] + resultado["Valor Fatura com Compensação (R$)"]
    )
    
    resultado.update(extrair_saldos_scee(texto))
    
    if resultado["Consumo (kWh)"] > 0:
        resultado["Percentual Compensado (%)"] = round(
            (resultado["Energia Compensada (kWh)"] / resultado["Consumo (kWh)"]) * 100, 1
        )
    
    return resultado

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: DATA EXTRACTION - GENERATING PLANT
# ═══════════════════════════════════════════════════════════════════════════

def extrair_pagina_geradora(textos_paginas: List[str], arquivo: str) -> Optional[Dict]:
    """Extract generating plant data"""
    ano_emissao, mes_emissao = extrair_mes_ano_fatura(arquivo)
    ano_ref, mes_ref = calcular_mes_referencia(ano_emissao, mes_emissao)
    
    geracao = {
        "Tipo Registro": "GERACAO/COMPENSACAO",
        "Fatura": arquivo,
        "Número Fatura": None,
        "Ano Referência": ano_ref,
        "Mês Referência": mes_ref,
        "UC Geradora": Config.UC_GERADORA,
        "UC Beneficiária": Config.UC_GERADORA,
        "Flag Geração Própria": 1,
        "Flag Uso Crédito": 0,
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
    
    kwh_gerado_pt = kwh_gerado_fp = 0.0
    
    for texto in textos_paginas:
        if Config.UC_GERADORA not in texto:
            continue
        if "GERAC kWh" not in texto and "ENERGIA GERADA/INJETADA" not in texto:
            continue
        
        # Extract fields
        if not geracao["Número Fatura"]:
            geracao["Número Fatura"] = extrair_numero_fatura(texto)
        
        if geracao["Flag Uso Crédito"] == 0:
            geracao["Flag Uso Crédito"] = verificar_uso_credito(texto)
        
        if not geracao["Grupo Tensão"]:
            geracao["Grupo Tensão"] = extrair_grupo_tensao(texto)
        
        if not geracao["Valor Fatura com Compensação (R$)"]:
            m = re.search(rf"{Config.UC_GERADORA}\s+\d{{2}}/\d{{4}}\s+\d{{2}}/\d{{2}}/\d{{4}}\s+R\$([\d.,]+)", texto)
            if not m:
                m = re.search(r"\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$([\d.,]+)", texto)
            if m:
                geracao["Valor Fatura com Compensação (R$)"] = to_float(m.group(1))
        
        if geracao["Saldo Crédito Anterior (R$)"] == 0.0:
            geracao["Saldo Crédito Anterior (R$)"] = extrair_saldo_credito_anterior(texto)
        
        if geracao["Consumo (kWh)"] == 0.0:
            m_cons = re.search(r"\d{7,10}\s+CONSUMO kWh TP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
            if m_cons:
                geracao["Consumo (kWh)"] = to_float(m_cons.group(1))
        
        # Generation (GERAC)
        m_pt = re.search(r"\d{7,10}\s+GERAC kWh PT\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
        if m_pt:
            kwh_gerado_pt = to_float(m_pt.group(1))
        
        m_fp = re.search(r"\d{7,10}\s+GERAC kWh FP\s+\d+\s+\d+\s+[\d.]+\s+([\d.,]+)", texto)
        if m_fp:
            kwh_gerado_fp = to_float(m_fp.group(1))
        
        # Alternative generation extraction
        if kwh_gerado_pt == 0:
            m_ext_pt = re.search(r"ENERGIA GERADA/INJETADA PONTA\s+\d+\s+\d+\s+([\d.,]+)", texto)
            if m_ext_pt:
                kwh_gerado_pt = to_float(m_ext_pt.group(1))
        
        if kwh_gerado_fp == 0:
            m_ext_fp = re.search(r"ENERGIA GERADA/INJETADA FORA P\s+\d+\s+\d+\s+([\d.,]+)", texto)
            if m_ext_fp:
                kwh_gerado_fp = to_float(m_ext_fp.group(1))
        
        # Compensation at plant
        if "ENERGIA INJETADA" in texto.upper():
            kwh_comp, valor_comp, meses_comp = extrair_valores_energia_inj(texto, "ENERGIA INJETADA")
            if kwh_comp > 0:
                geracao["Energia Compensada (kWh)"] = kwh_comp
            if valor_comp > 0:
                geracao["Valor Compensado (R$)"] = valor_comp
            if meses_comp:
                geracao["Meses Compensados"] = meses_comp
        
        # SCEE balances
        saldos = extrair_saldos_scee(texto)
        if saldos["Saldo Mês Ponta"] > 0 or saldos["Saldo Mês FP"] > 0:
            geracao.update(saldos)
    
    # Finalization
    geracao["Energia Gerada (kWh)"] = kwh_gerado_pt + kwh_gerado_fp
    geracao["Valor Fatura sem Compensação (R$)"] = (
        geracao["Valor Compensado (R$)"] + geracao["Valor Fatura com Compensação (R$)"]
    )
    
    if geracao["Energia Gerada (kWh)"] > 0:
        geracao["Percentual Compensado (%)"] = round(
            (geracao["Energia Compensada (kWh)"] / geracao["Energia Gerada (kWh)"]) * 100, 1
        )
    
    if geracao["Energia Gerada (kWh)"] == 0 and geracao["Valor Fatura com Compensação (R$)"] == 0:
        return None
    
    return geracao

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: PDF PROCESSING
# ═══════════════════════════════════════════════════════════════════════════

def extrair_paginas_filtradas(caminho_pdf: Path, caminho_saida: Path, logger: logging.Logger) -> bool:
    """Extract only relevant pages from PDF"""
    try:
        reader = PdfReader(str(caminho_pdf))
        writer = PdfWriter()
        paginas_encontradas = 0
        
        with pdfplumber.open(str(caminho_pdf)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    texto = page.extract_text() or ""
                    if not texto.strip():
                        continue
                    
                    if contem_uc_geradora(texto) or tem_energia_injetada(texto):
                        writer.add_page(reader.pages[i])
                        paginas_encontradas += 1
                except Exception:
                    continue
        
        if paginas_encontradas == 0:
            logger.warning(f"No valid pages found: {caminho_pdf.name}")
            return False
        
        caminho_saida.parent.mkdir(parents=True, exist_ok=True)
        with caminho_saida.open('wb') as f:
            writer.write(f)
        
        logger.info(f"✅ {caminho_pdf.name} → {paginas_encontradas} pages")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error processing {caminho_pdf.name}: {e}")
        return False

def processar_limpeza(origem: Path, limpos: Path, logger: logging.Logger, 
                     teste_mes_ano: Optional[Tuple[int, int]] = None):
    """Process PDF cleaning"""
    logger.info("=" * 60)
    logger.info("STAGE 1: PDF CLEANING")
    logger.info("=" * 60)
    
    processados = ignorados = 0
    arquivos = sorted(
        [(f.name, f) for f in origem.glob('**/*.pdf')],
        key=lambda x: extrair_mes_ano_fatura(x[0])
    )
    
    if teste_mes_ano:
        ano_teste, mes_teste = teste_mes_ano
        arquivos = [
            (f, c) for f, c in arquivos
            if extrair_mes_ano_fatura(f) == (ano_teste, mes_teste)
        ]
        logger.info(f"🧪 TEST MODE: Only {ano_teste}/{mes_teste:02d}")
    
    logger.info(f"Total files to process: {len(arquivos)}")
    
    for i, (file, caminho_origem) in enumerate(arquivos, 1):
        logger.info(f"[{i}/{len(arquivos)}] Processing {file}")
        
        if not arquivo_valido(file):
            logger.info(f"  ⏭️  Ignored (out of period)")
            ignorados += 1
            continue
        
        rel_path = caminho_origem.relative_to(origem)
        caminho_destino = limpos / rel_path
        
        if caminho_destino.exists():
            logger.info(f"  ⏭️  Already processed")
            continue
        
        try:
            if extrair_paginas_filtradas(caminho_origem, caminho_destino, logger):
                processados += 1
            else:
                ignorados += 1
        except Exception as e:
            logger.error(f"  ❌ Error: {e}")
            ignorados += 1
    
    logger.info("=" * 60)
    logger.info(f"Total: {len(arquivos)} | Processed: {processados} | Ignored: {ignorados}")
    logger.info("=" * 60)

def processar_pdf(caminho_pdf: Path, logger: logging.Logger) -> Dict:
    """Process complete PDF with improved page grouping"""
    arquivo = caminho_pdf.name
    
    # Group pages by UC
    paginas_por_uc = {}
    todos_textos = []
    uc_anterior = None
    
    with pdfplumber.open(str(caminho_pdf)) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            todos_textos.append(texto)
            
            uc = extrair_uc_beneficiaria(texto)
            if not uc and uc_anterior:
                uc = uc_anterior
            
            if uc:
                if uc not in paginas_por_uc:
                    paginas_por_uc[uc] = []
                paginas_por_uc[uc].append(texto)
                uc_anterior = uc
    
    # Process beneficiaries
    beneficiarias = []
    for uc, textos_uc in paginas_por_uc.items():
        if uc == Config.UC_GERADORA:
            continue
        
        texto_completo = "\n".join(textos_uc)
        dados_bene = extrair_pagina_beneficiaria(texto_completo, arquivo)
        
        if dados_bene and dados_bene["UC Beneficiária"]:
            # Search for invoice number in all pages
            if not dados_bene["Número Fatura"]:
                for texto_pagina in textos_uc:
                    numero_fatura = extrair_numero_fatura(texto_pagina)
                    if numero_fatura:
                        dados_bene["Número Fatura"] = numero_fatura
                        break
            
            beneficiarias.append(dados_bene)
    
    # Process generator
    dados_geradora = extrair_pagina_geradora(todos_textos, arquivo)
    
    return {
        "beneficiarias": beneficiarias,
        "geradora": dados_geradora
    }

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: DATA CONSOLIDATION
# ═══════════════════════════════════════════════════════════════════════════

def consolidar_dados(limpos: Path, logger: logging.Logger,
                    teste_mes_ano: Optional[Tuple[int, int]] = None) -> pd.DataFrame:
    """Consolidate all extracted data"""
    logger.info("=" * 60)
    logger.info("STAGE 2: DATA EXTRACTION & CONSOLIDATION")
    logger.info("=" * 60)
    
    registros = []
    arquivos = sorted(
        limpos.glob('**/*.pdf'),
        key=lambda x: extrair_mes_ano_fatura(x.name)
    )
    
    if teste_mes_ano:
        ano_teste, mes_teste = teste_mes_ano
        arquivos = [
            f for f in arquivos
            if extrair_mes_ano_fatura(f.name) == (ano_teste, mes_teste)
        ]
        logger.info(f"🧪 TEST MODE: Only {ano_teste}/{mes_teste:02d}")
    
    logger.info(f"Processing {len(arquivos)} file(s)...")
    
    for caminho in arquivos:
        try:
            resultado = processar_pdf(caminho, logger)
            n_bene = len(resultado["beneficiarias"])
            tem_ger = resultado["geradora"] is not None
            
            registros.extend(resultado["beneficiarias"])
            if resultado["geradora"]:
                registros.append(resultado["geradora"])
            
            logger.info(f"  ✅ {caminho.name}: {n_bene} beneficiaries | generator: {'✅' if tem_ger else '—'}")
        
        except Exception as e:
            logger.error(f"  ❌ Error processing {caminho.name}: {e}")
    
    df = pd.DataFrame(registros)
    
    if not df.empty:
        # Format columns
        int_cols = [
            "Consumo (kWh)", "Energia Compensada (kWh)", "Energia Gerada (kWh)",
            "Saldo Mês Ponta", "Saldo Mês FP", "Saldo Acumulado Ponta",
            "Saldo Acumulado FP", "Saldo a Expirar Ponta", "Saldo a Expirar FP"
        ]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
        
        float_cols = [
            "Valor Compensado (R$)", "Valor Fatura com Compensação (R$)",
            "Valor Fatura sem Compensação (R$)", "Saldo Crédito Anterior (R$)"
        ]
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0).round(2)
        
        if "Percentual Compensado (%)" in df.columns:
            df["Percentual Compensado (%)"] = pd.to_numeric(
                df["Percentual Compensado (%)"], errors='coerce'
            ).fillna(0.0).round(1)
        
        if "Meses Compensados" in df.columns:
            df["Meses Compensados (Texto)"] = df["Meses Compensados"].apply(
                lambda x: " | ".join(x) if isinstance(x, list) and x else ""
            )
    
    return df

def gerar_resumo_mensal(df: pd.DataFrame) -> pd.DataFrame:
    """Generate monthly summary"""
    if df.empty:
        return pd.DataFrame()
    
    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)].copy()
    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"].copy()
    
    if not df_ger.empty:
        resumo_ger = df_ger.groupby(["Ano Referência", "Mês Referência"]).agg({
            "Energia Gerada (kWh)": "sum",
            "Energia Compensada (kWh)": "sum",
        }).reset_index()
        resumo_ger.rename(columns={"Energia Compensada (kWh)": "kWh Compensado na Usina"}, inplace=True)
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
        resumo = resumo_ger.merge(resumo_comp, on=["Ano Referência", "Mês Referência"], how="outer")
    elif not resumo_ger.empty:
        resumo = resumo_ger
    else:
        resumo = resumo_comp
    
    return resumo.sort_values(["Ano Referência", "Mês Referência"])

def salvar_outputs(df: pd.DataFrame, output_dir: Path, logger: logging.Logger, 
                  sufixo: str = "", drive_manager: Optional[GoogleDriveManager] = None):
    """Save outputs and optionally upload to Drive"""
    if df.empty:
        logger.warning("Empty DataFrame, nothing to save")
        return
    
    logger.info("=" * 60)
    logger.info("STAGE 3: SAVING OUTPUTS")
    logger.info("=" * 60)
    
    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)].copy()
    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"].copy()
    
    df_save = df.copy()
    if "Meses Compensados" in df_save.columns:
        df_save = df_save.drop(columns=["Meses Compensados"])
    
    # Save files
    arquivos_salvos = []
    
    if not df_ger.empty:
        p_ger = output_dir / f"geracao{sufixo}.csv"
        df_ger_save = df_ger.copy()
        if "Meses Compensados" in df_ger_save.columns:
            df_ger_save = df_ger_save.drop(columns=["Meses Compensados"])
        df_ger_save.to_csv(p_ger, index=False, encoding='utf-8-sig')
        logger.info(f"💾 Generation: {p_ger.name}")
        arquivos_salvos.append(p_ger)
    
    if not df_comp.empty:
        p_comp = output_dir / f"compensacao{sufixo}.csv"
        df_comp_save = df_comp.copy()
        if "Meses Compensados" in df_comp_save.columns:
            df_comp_save = df_comp_save.drop(columns=["Meses Compensados"])
        df_comp_save.to_csv(p_comp, index=False, encoding='utf-8-sig')
        logger.info(f"💾 Compensation: {p_comp.name}")
        arquivos_salvos.append(p_comp)
    
    df_resumo = gerar_resumo_mensal(df)
    if not df_resumo.empty:
        p_resumo = output_dir / f"resumo_mensal{sufixo}.csv"
        df_resumo.to_csv(p_resumo, index=False, encoding='utf-8-sig')
        logger.info(f"💾 Monthly summary: {p_resumo.name}")
        arquivos_salvos.append(p_resumo)
    
    p_completo = output_dir / f"base_completa{sufixo}.csv"
    df_save.to_csv(p_completo, index=False, encoding='utf-8-sig')
    logger.info(f"💾 Complete database: {p_completo.name}")
    arquivos_salvos.append(p_completo)
    
    # Upload to Google Drive if available
    if drive_manager and not sufixo:  # Only upload in production mode
        try:
            drive_manager.upload_results(Config.GOOGLE_DRIVE_FOLDER_ID, output_dir)
        except Exception as e:
            logger.error(f"❌ Failed to upload to Drive: {e}")

def imprimir_resumo(df: pd.DataFrame, logger: logging.Logger):
    """Print data summary"""
    if df.empty:
        logger.warning("No data extracted")
        return
    
    logger.info("=" * 60)
    logger.info("DATA SUMMARY")
    logger.info("=" * 60)
    
    logger.info("\n📌 BY RECORD TYPE:")
    logger.info(f"\n{df['Tipo Registro'].value_counts().to_string()}")
    
    df_ger = df[df["Tipo Registro"].str.contains("GERACAO", na=False)]
    if not df_ger.empty:
        logger.info("\n⚡ GENERATION (PLANT):")
        logger.info(f"  Total generated: {df_ger['Energia Gerada (kWh)'].sum():,.0f} kWh")
        logger.info(f"  Compensated at plant: {df_ger['Energia Compensada (kWh)'].sum():,.0f} kWh")
    
    df_comp = df[df["Tipo Registro"] == "COMPENSACAO"]
    if not df_comp.empty:
        logger.info("\n🔋 COMPENSATION (UCs):")
        logger.info(f"  Beneficiary UCs: {df_comp['UC Beneficiária'].nunique()}")
        logger.info(f"  Total compensated: {df_comp['Energia Compensada (kWh)'].sum():,.0f} kWh")
        logger.info(f"  Compensation value: R$ {df_comp['Valor Compensado (R$)'].sum():,.2f}")
        logger.info(f"  Total invoices: R$ {df_comp['Valor Fatura com Compensação (R$)'].sum():,.2f}")
    
    if "Saldo Acumulado FP" in df.columns:
        logger.info("\n💰 MAXIMUM BALANCES:")
        logger.info(f"  Peak: {df['Saldo Acumulado Ponta'].max():,} kWh")
        logger.info(f"  Off-peak: {df['Saldo Acumulado FP'].max():,} kWh")
    
    logger.info("=" * 60)

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: MAIN PIPELINE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def executar_pipeline(debug: bool = True, salvar: bool = True, 
                     modo_teste: bool = False, mes_teste: int = 3, ano_teste: int = 2026):
    """Execute complete pipeline"""
    # Initialize environment
    env = Config.detect_environment()
    logger = setup_logging(env)
    
    logger.info("=" * 60)
    logger.info("🚀 COPEL ENERGY COMPENSATION PIPELINE v5.0")
    logger.info("=" * 60)
    
    # Setup paths
    paths = Config.setup_paths(env)
    logger.info(f"📁 Source: {paths['origem']}")
    logger.info(f"📁 Cleaned: {paths['limpos']}")
    logger.info(f"📁 Output: {paths['output']}")
    
    # Initialize Google Drive (GitHub Actions only)
    drive_manager = None
    if env == 'github_actions':
        try:
            drive_manager = GoogleDriveManager(logger)
            drive_manager.download_pdfs(Config.GOOGLE_DRIVE_FOLDER_ID, paths['origem'])
        except Exception as e:
            logger.error(f"❌ Google Drive error: {e}")
            logger.info("Continuing with local files...")
    elif env == 'colab':
        from google.colab import drive as colab_drive
        colab_drive.mount('/content/drive', force_remount=True)
    
    # Test mode configuration
    teste_mes_ano = (ano_teste, mes_teste) if modo_teste else None
    
    # Execute pipeline stages
    processar_limpeza(paths['origem'], paths['limpos'], logger, teste_mes_ano)
    df = consolidar_dados(paths['limpos'], logger, teste_mes_ano)
    
    if debug:
        imprimir_resumo(df, logger)
        if not df.empty:
            logger.info("\n🔎 DATA SAMPLE (first 10 rows):")
            print(df.head(10).to_string())
    
    if salvar and not df.empty:
        sufixo = f"_{ano_teste}-{mes_teste:02d}" if modo_teste else ""
        salvar_outputs(df, paths['output'], logger, sufixo, drive_manager)
    
    logger.info("=" * 60)
    logger.info("✅ PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("=" * 60)
    
    return df

# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        df_resultado = executar_pipeline(
            debug=True,
            salvar=True,
            modo_teste=False,  # Set to False for PRODUCTION
            mes_teste=3,
            ano_teste=2026
        )
        sys.exit(0)  # Success
    except Exception as e:
        logging.error(f"❌ PIPELINE FAILED: {e}")
        sys.exit(1)  # Failure

