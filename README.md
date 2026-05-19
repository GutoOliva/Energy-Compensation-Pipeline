# COPEL Energy Compensation Pipeline

Automated pipeline for processing COPEL energy invoices and compensation data.

## 🚀 Features

- Automated PDF processing
- Data extraction and consolidation
- Google Drive integration
- GitHub Actions automation

## 🔧 Setup

### Local Development (Google Colab)
1. Open in Google Colab
2. Run all cells
3. Data saved to Google Drive

### Production (GitHub Actions)
1. Configure `GOOGLE_CREDENTIALS` secret
2. Pipeline runs automatically daily at 2 AM BRT
3. Results uploaded to Google Drive

## 📊 Output Files

- `base_completa.csv` - Complete dataset
- `geracao.csv` - Generation data
- `compensacao.csv` - Compensation data
- `resumo_mensal.csv` - Monthly summary

## 🔐 Required Secrets

- `GOOGLE_CREDENTIALS` - Service Account JSON

## 📅 Schedule

Runs daily at 2 AM BRT (5 AM UTC)

## 🛠️ Manual Execution

Go to **Actions** → **Run workflow**
