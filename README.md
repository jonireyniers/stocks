# 📈 Stock Dashboard

Een professionele multi-user stock portfolio management applicatie gebouwd met Streamlit.

## Features

### 📁 Portfolio Overzicht
- Volledige portfolio tracking met real-time prijzen
- Fair Value berekening (3-methode gewogen)
- BUY/HOLD/SELL signalen
- Sector allocatie visualisatie

### 🎯 Stock Aanbevelingen
- Enterprise-grade scoring systeem (0-20 punten)
- 60+ populaire stocks gescand
- Fundamentele + technische analyse
- Risk/reward berekening

### 📊 Advanced Analytics
- VaR (Value at Risk) 95%
- Sharpe Ratio
- Max Drawdown
- Beta vs S&P 500
- Portfolio correlatie matrix

### 🔔 Price Alerts
- Stel prijs alerts in per stock
- Boven/onder target notificaties

### 💰 Income Tracking
- Dividend yield tracking
- Annual income berekening
- S&P 500 benchmark vergelijking

### 🧮 Position Calculator
- Risk-based position sizing
- Kelly Criterion calculator
- Stop-loss suggesties

## Installatie

```bash
# Clone de repository
git clone https://github.com/jonireyniers/stocks.git
cd stocks

# Maak een virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# of: .venv\Scripts\activate  # Windows

# Installeer dependencies
pip install streamlit yfinance pandas numpy ta plotly

# Start de app
streamlit run app.py
```

## Gebruik

1. Open http://localhost:8501
2. Maak een nieuwe gebruiker of log in
3. Voeg stocks toe aan je portfolio (TICKER, AANTAL, PRIJS, MUNT)
4. Bekijk analyses, aanbevelingen en alerts

## Tech Stack

- **Streamlit** - Web framework
- **yfinance** - Financial data API
- **pandas** - Data manipulation
- **ta** - Technical analysis
- **plotly** - Interactive charts

## Disclaimer

⚠️ Dit is geen financieel advies. Doe altijd je eigen onderzoek (DYOR) voordat je investeringsbeslissingen neemt.

## License

MIT
