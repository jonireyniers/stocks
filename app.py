import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta as ta_lib
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import io
import base64
import csv
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Multi-user storage ────────────────────────────────────────────────────────
DATA_DIR = Path("portfolios")
DATA_DIR.mkdir(exist_ok=True)

def load_user_portfolio(username: str) -> dict:
    """Load user's portfolio from JSON file."""
    filepath = DATA_DIR / f"{username}.json"
    if filepath.exists():
        with open(filepath, "r") as f:
            data = json.load(f)
            # Ensure new fields exist
            if "price_alerts" not in data:
                data["price_alerts"] = []
            if "portfolio_history" not in data:
                data["portfolio_history"] = []
            if "watchlist_categories" not in data:
                data["watchlist_categories"] = {}
            if "youtubers" not in data:
                data["youtubers"] = []
            if "youtuber_picks" not in data:
                data["youtuber_picks"] = []
            if "analyzed_videos" not in data:
                data["analyzed_videos"] = []
            return data
    return {"portfolio_raw": "", "watchlist_raw": "", "price_alerts": [], "portfolio_history": [], "watchlist_categories": {}, "youtubers": [], "youtuber_picks": [], "analyzed_videos": [], "email_config": {}}

def save_user_portfolio(username: str, data: dict):
    """Save user's portfolio to JSON file."""
    filepath = DATA_DIR / f"{username}.json"
    with open(filepath, "w") as f:
        json.dump(data, f)

def get_all_users() -> list:
    """Get list of all registered users."""
    return [f.stem for f in DATA_DIR.glob("*.json")]


# ── Email Functions ───────────────────────────────────────────────────────────
def send_email_alert(to_email: str, subject: str, body: str, email_config: dict) -> tuple:
    """
    Send email alert using SMTP.
    Returns (success: bool, message: str)
    
    Supports:
    - Gmail (requires App Password)
    - Outlook/Hotmail
    - Custom SMTP servers
    """
    try:
        smtp_server = email_config.get("smtp_server", "smtp.gmail.com")
        smtp_port = email_config.get("smtp_port", 587)
        sender_email = email_config.get("sender_email", "")
        sender_password = email_config.get("sender_password", "")
        
        if not sender_email or not sender_password:
            return False, "Email configuratie incompleet. Stel sender email en wachtwoord in."
        
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = to_email
        
        # HTML body
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background: #0d1117; color: #e6edf3; padding: 20px; }}
                .container {{ background: #161b22; border-radius: 12px; padding: 20px; max-width: 600px; margin: 0 auto; }}
                .header {{ color: #58a6ff; font-size: 24px; font-weight: bold; margin-bottom: 20px; }}
                .content {{ line-height: 1.6; }}
                .footer {{ margin-top: 20px; padding-top: 15px; border-top: 1px solid #30363d; font-size: 12px; color: #8b949e; }}
                .alert-box {{ background: #0d2318; border: 2px solid #3fb950; border-radius: 8px; padding: 15px; margin: 10px 0; }}
                .warning-box {{ background: #3d1515; border: 2px solid #f85149; border-radius: 8px; padding: 15px; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">📈 Stock Dashboard Alert</div>
                <div class="content">
                    {body}
                </div>
                <div class="footer">
                    Verzonden door Stock Dashboard · {datetime.now().strftime('%d %b %Y, %H:%M')}
                </div>
            </div>
        </body>
        </html>
        """
        
        # Attach both plain text and HTML
        plain_text = body.replace("<br>", "\n").replace("<b>", "").replace("</b>", "")
        part1 = MIMEText(plain_text, "plain")
        part2 = MIMEText(html_body, "html")
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        
        return True, f"✅ Email verzonden naar {to_email}"
    
    except smtplib.SMTPAuthenticationError:
        return False, "❌ Authenticatie mislukt. Controleer email en wachtwoord (gebruik App Password voor Gmail)."
    except smtplib.SMTPException as e:
        return False, f"❌ SMTP fout: {str(e)}"
    except Exception as e:
        return False, f"❌ Fout bij verzenden: {str(e)}"


def build_alert_email_body(triggered_alerts: list, ticker_data: dict) -> str:
    """Build HTML email body for triggered alerts."""
    body = "<h2>🚨 De volgende alerts zijn getriggerd:</h2>"
    
    for alert in triggered_alerts:
        ticker = alert.get("ticker", "???")
        current_price = 0
        df = ticker_data.get(ticker, pd.DataFrame())
        if not df.empty and "Close" in df.columns:
            current_price = float(df["Close"].iloc[-1])
        
        alert_type_str = "📈 Boven target" if alert.get("type") == "above" else "📉 Onder target"
        
        body += f"""
        <div class="alert-box">
            <b style="font-size:18px; color:#3fb950">{ticker}</b><br>
            <b>Type:</b> {alert_type_str}<br>
            <b>Target:</b> ${alert.get('target_price', 0):.2f}<br>
            <b>Huidige prijs:</b> ${current_price:.2f}<br>
        </div>
        """
    
    body += "<br><p>Log in op je Stock Dashboard voor meer details.</p>"
    return body


def build_portfolio_summary_email(portfolio_positions: list, ticker_data: dict) -> str:
    """Build HTML email body for daily portfolio summary."""
    total_value = 0
    total_cost = 0
    positions_html = ""
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        qty = pos["qty"]
        gak = pos["gak"]
        
        df = ticker_data.get(ticker, pd.DataFrame())
        current_price = float(df["Close"].iloc[-1]) if not df.empty and "Close" in df.columns else 0
        
        value = current_price * qty
        cost = gak * qty
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        
        total_value += value
        total_cost += cost
        
        color = "#3fb950" if pnl >= 0 else "#f85149"
        
        positions_html += f"""
        <tr>
            <td style="padding:8px; border-bottom:1px solid #30363d"><b>{ticker}</b></td>
            <td style="padding:8px; border-bottom:1px solid #30363d">${current_price:.2f}</td>
            <td style="padding:8px; border-bottom:1px solid #30363d">{qty:.2f}</td>
            <td style="padding:8px; border-bottom:1px solid #30363d">${value:.2f}</td>
            <td style="padding:8px; border-bottom:1px solid #30363d; color:{color}">{pnl_pct:+.2f}%</td>
        </tr>
        """
    
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    total_color = "#3fb950" if total_pnl >= 0 else "#f85149"
    
    body = f"""
    <h2>📊 Portfolio Overzicht</h2>
    <p><b>Datum:</b> {datetime.now().strftime('%d %B %Y')}</p>
    
    <div style="display:flex; gap:20px; margin:15px 0">
        <div style="background:#161b22; padding:15px; border-radius:8px; flex:1">
            <div style="color:#8b949e; font-size:12px">Totaal Geïnvesteerd</div>
            <div style="font-size:20px; font-weight:bold">${total_cost:.2f}</div>
        </div>
        <div style="background:#161b22; padding:15px; border-radius:8px; flex:1">
            <div style="color:#8b949e; font-size:12px">Huidige Waarde</div>
            <div style="font-size:20px; font-weight:bold">${total_value:.2f}</div>
        </div>
        <div style="background:#161b22; padding:15px; border-radius:8px; flex:1">
            <div style="color:#8b949e; font-size:12px">Totaal W/V</div>
            <div style="font-size:20px; font-weight:bold; color:{total_color}">${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)</div>
        </div>
    </div>
    
    <h3>📋 Posities</h3>
    <table style="width:100%; border-collapse:collapse">
        <tr style="background:#21262d">
            <th style="padding:10px; text-align:left">Ticker</th>
            <th style="padding:10px; text-align:left">Prijs</th>
            <th style="padding:10px; text-align:left">Aantal</th>
            <th style="padding:10px; text-align:left">Waarde</th>
            <th style="padding:10px; text-align:left">W/V %</th>
        </tr>
        {positions_html}
    </table>
    """
    
    return body


# ── Custom CSS ────────────────────────────────────────────────────────────────

# Mobile viewport meta tag for proper scaling
st.markdown("""
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
    /* Global responsive base styles */
    * { box-sizing: border-box; }
    html, body { 
        -webkit-text-size-adjust: 100%; 
        touch-action: manipulation;
    }
    
    /* Prevent horizontal scroll */
    .main .block-container { 
        max-width: 100% !important; 
        overflow-x: hidden;
    }
    
    /* Make all images responsive */
    img { max-width: 100%; height: auto; }
    
    /* Touch-friendly tap targets */
    button, a, input, select { min-height: 44px; }
    
    /* Scrollable tables on mobile */
    .stDataFrame { 
        overflow-x: auto; 
        -webkit-overflow-scrolling: touch;
    }
    
    /* Better touch scrolling for tabs */
    [data-baseweb="tab-list"] {
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
        -ms-overflow-style: none;
    }
    [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
</style>
""", unsafe_allow_html=True)

# Initialize theme in session state
if "theme" not in st.session_state:
    st.session_state.theme = "dark"  # Default to dark mode

# Theme-specific CSS
if st.session_state.theme == "dark":
    theme_css = """
    <style>
      /* ════════════════════════════════════════════════════════════════════════
         DARK MODE - Diepe zwarte/blauwe tinten met heldere accenten
         ════════════════════════════════════════════════════════════════════════ */
      
      /* Main backgrounds - deep dark */
      .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"], 
      .main, .main .block-container { 
          background-color: #0a0e14 !important; 
      }

      /* Metric cards - dark panels with blue glow */
      div[data-testid="metric-container"] {
          background: linear-gradient(145deg, #141a24, #0f1319) !important;
          border: 1px solid #1e3a5f !important;
          border-radius: 14px;
          padding: 20px 26px;
          box-shadow: 0 4px 20px rgba(30, 58, 95, 0.3);
      }
      div[data-testid="metric-container"] label {
          color: #7eb8da !important;
          font-size: 0.78rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
      }
      div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
          font-size: 1.7rem;
          font-weight: 700;
          color: #ffffff !important;
      }

      /* Tab styling - glowing tabs */
      [data-baseweb="tab-list"] {
          background-color: #0f1419 !important;
          border-radius: 10px;
          padding: 4px;
      }
      button[data-baseweb="tab"] {
          font-size: 0.92rem;
          font-weight: 600;
          color: #7a8599 !important;
          background-color: transparent !important;
      }
      button[data-baseweb="tab"][aria-selected="true"] {
          color: #58a6ff !important;
          background-color: #1a2332 !important;
          border-radius: 8px;
          border-bottom: 3px solid #58a6ff !important;
      }

      /* DataFrame */
      .stDataFrame { 
          border-radius: 12px; 
          overflow: hidden;
          border: 1px solid #1e3a5f;
      }

      /* Sidebar - darker panel */
      section[data-testid="stSidebar"] { 
          background: linear-gradient(180deg, #0d1117, #0a0e14) !important;
          border-right: 1px solid #1e3a5f;
      }
      section[data-testid="stSidebar"] h1,
      section[data-testid="stSidebar"] h2,
      section[data-testid="stSidebar"] h3,
      section[data-testid="stSidebar"] p,
      section[data-testid="stSidebar"] span,
      section[data-testid="stSidebar"] label {
          color: #c9d1d9 !important;
      }

      /* Section headers - bright white */
      h1, h2, h3, h4 { color: #ffffff !important; }
      
      /* Text colors - light gray */
      p, span, label, .stMarkdown { color: #b0b8c4 !important; }

      /* Status badges - vivid colors */
      .badge-buy  { background:#0d3320; color:#2dd272; padding:4px 12px; border-radius:20px; font-weight:700; font-size:.82rem; border: 1px solid #2dd272; }
      .badge-sell { background:#3d1515; color:#ff6b6b; padding:4px 12px; border-radius:20px; font-weight:700; font-size:.82rem; border: 1px solid #ff6b6b; }
      .badge-hold { background:#3d3010; color:#ffd43b; padding:4px 12px; border-radius:20px; font-weight:700; font-size:.82rem; border: 1px solid #ffd43b; }
      
      /* Info boxes */
      [data-testid="stAlert"] {
          background-color: #141a24 !important;
          border: 1px solid #1e3a5f !important;
          border-radius: 10px;
      }
      
      /* Expander */
      .streamlit-expanderHeader, details summary { 
          background-color: #141a24 !important;
          color: #c9d1d9 !important;
      }
      
      /* Inputs - dark with blue border */
      .stTextInput input, 
      .stNumberInput input,
      .stTextArea textarea,
      .stSelectbox [data-baseweb="select"] > div {
          background-color: #0f1419 !important;
          color: #e6edf3 !important;
          border: 1px solid #1e3a5f !important;
          border-radius: 8px;
      }
      .stTextInput input:focus, .stNumberInput input:focus {
          border-color: #58a6ff !important;
          box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2) !important;
      }
      
      /* Buttons */
      .stButton button {
          background: linear-gradient(145deg, #1a2332, #141a24) !important;
          color: #c9d1d9 !important;
          border: 1px solid #1e3a5f !important;
          border-radius: 8px;
          font-weight: 600;
      }
      .stButton button:hover {
          background: linear-gradient(145deg, #1e2a3a, #1a2332) !important;
          border-color: #58a6ff !important;
          color: #ffffff !important;
      }
      .stButton button[kind="primary"] {
          background: linear-gradient(145deg, #1a5fb4, #1256a0) !important;
          color: #ffffff !important;
          border: none !important;
      }
      
      /* Dividers */
      hr { border-color: #1e3a5f !important; }
      
      /* Radio & Checkbox */
      .stRadio label, .stCheckbox label { color: #c9d1d9 !important; }
      
      /* Caption */
      .stCaption, [data-testid="stCaptionContainer"] { color: #7a8599 !important; }
      
      /* ════════════════════════════════════════════════════════════════════════
         RESPONSIVE - Tablet & Mobile optimizations (Dark Mode)
         ════════════════════════════════════════════════════════════════════════ */
      
      /* Tablet breakpoint */
      @media (max-width: 1024px) {
          .main .block-container { padding: 1rem 1.5rem !important; }
          div[data-testid="metric-container"] { padding: 15px 18px; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1.4rem; }
          h1 { font-size: 1.6rem !important; }
          h2 { font-size: 1.3rem !important; }
          button[data-baseweb="tab"] { font-size: 0.8rem; padding: 8px 10px; }
      }
      
      /* Mobile breakpoint */
      @media (max-width: 768px) {
          /* Reduce padding */
          .main .block-container { padding: 0.5rem 0.8rem !important; }
          
          /* Stack columns vertically */
          [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
          
          /* Metric cards - full width, smaller text */
          div[data-testid="metric-container"] { 
              padding: 12px 14px; 
              margin-bottom: 8px;
          }
          div[data-testid="metric-container"] label { font-size: 0.7rem; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1.2rem; }
          
          /* Headers smaller */
          h1 { font-size: 1.4rem !important; }
          h2 { font-size: 1.2rem !important; }
          h3 { font-size: 1rem !important; }
          
          /* Tabs - scrollable, smaller */
          [data-baseweb="tab-list"] { 
              overflow-x: auto; 
              -webkit-overflow-scrolling: touch;
              padding: 4px;
          }
          button[data-baseweb="tab"] { 
              font-size: 0.7rem; 
              padding: 6px 8px; 
              white-space: nowrap;
              min-width: auto;
          }
          
          /* Sidebar - minimize */
          section[data-testid="stSidebar"] { 
              width: 260px !important; 
          }
          
          /* Buttons - full width on mobile */
          .stButton button { 
              width: 100% !important; 
              padding: 12px !important;
              font-size: 0.9rem;
          }
          
          /* DataFrames - scroll horizontally */
          .stDataFrame { overflow-x: auto !important; }
          
          /* Charts - reduce height */
          [data-testid="stPlotlyChart"] { min-height: 250px !important; }
          
          /* Info boxes */
          [data-testid="stAlert"] { 
              padding: 10px !important; 
              font-size: 0.85rem;
          }
          
          /* Input fields - larger touch targets */
          .stTextInput input, 
          .stNumberInput input,
          .stSelectbox [data-baseweb="select"] > div {
              min-height: 44px !important;
              font-size: 16px !important; /* Prevents zoom on iOS */
          }
      }
      
      /* Small mobile */
      @media (max-width: 480px) {
          .main .block-container { padding: 0.3rem 0.5rem !important; }
          h1 { font-size: 1.2rem !important; }
          h2 { font-size: 1.05rem !important; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1rem; }
          button[data-baseweb="tab"] { font-size: 0.65rem; padding: 5px 6px; }
      }
    </style>
    """
else:
    theme_css = """
    <style>
      /* ════════════════════════════════════════════════════════════════════════
         LIGHT MODE - Maximum contrast, bright & clean
         ════════════════════════════════════════════════════════════════════════ */
      
      /* Main backgrounds - pure bright white */
      .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"], 
      .main, .main .block-container,
      [data-testid="stVerticalBlock"],
      [data-testid="stHorizontalBlock"] { 
          background-color: #ffffff !important; 
      }

      /* Metric cards - bright white with strong shadow */
      div[data-testid="metric-container"] {
          background: #ffffff !important;
          border: 3px solid #0066cc !important;
          border-radius: 16px;
          padding: 22px 28px;
          box-shadow: 0 6px 20px rgba(0, 100, 180, 0.15);
      }
      div[data-testid="metric-container"] label {
          color: #003d7a !important;
          font-size: 0.82rem;
          text-transform: uppercase;
          letter-spacing: 0.12em;
          font-weight: 700;
      }
      div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
          font-size: 1.8rem;
          font-weight: 800;
          color: #000000 !important;
      }
      div[data-testid="metric-container"] [data-testid="stMetricDelta"] {
          color: #000000 !important;
          font-weight: 700;
      }
      div[data-testid="metric-container"] [data-testid="stMetricDelta"] svg {
          fill: #000000 !important;
      }

      /* Tab styling - high contrast tabs */
      [data-baseweb="tab-list"] {
          background-color: #e0ecf8 !important;
          border-radius: 12px;
          padding: 6px;
          border: 2px solid #0066cc;
      }
      button[data-baseweb="tab"] {
          font-size: 0.95rem;
          font-weight: 700;
          color: #003366 !important;
          background-color: transparent !important;
          padding: 10px 16px;
      }
      button[data-baseweb="tab"][aria-selected="true"] {
          color: #ffffff !important;
          background-color: #0066cc !important;
          border-radius: 8px;
          border-bottom: none !important;
          box-shadow: 0 4px 12px rgba(0, 102, 204, 0.4);
      }

      /* DataFrame */
      .stDataFrame { 
          border-radius: 14px; 
          overflow: hidden;
          border: 3px solid #0066cc;
          background-color: #ffffff !important;
      }
      .stDataFrame th {
          background-color: #0066cc !important;
          color: #ffffff !important;
      }

      /* Sidebar - bright blue accent */
      section[data-testid="stSidebar"] { 
          background: linear-gradient(180deg, #d0e4f7, #b8d4f0) !important;
          border-right: 4px solid #0066cc;
      }
      section[data-testid="stSidebar"] h1,
      section[data-testid="stSidebar"] h2,
      section[data-testid="stSidebar"] h3 {
          color: #003366 !important;
          font-weight: 800;
      }
      section[data-testid="stSidebar"] p,
      section[data-testid="stSidebar"] span,
      section[data-testid="stSidebar"] label,
      section[data-testid="stSidebar"] .stMarkdown {
          color: #000000 !important;
          font-weight: 500;
      }

      /* Section headers - black bold */
      h1 { color: #000000 !important; font-weight: 800; font-size: 2rem; }
      h2 { color: #003366 !important; font-weight: 700; }
      h3, h4 { color: #004080 !important; font-weight: 700; }
      
      /* Text colors - pure black */
      p, span, label, .stMarkdown { color: #000000 !important; }
      
      /* Links - bright blue */
      a { color: #0055cc !important; font-weight: 600; text-decoration: underline; }

      /* Status badges - vivid saturated colors */
      .badge-buy  { background:#00cc44; color:#ffffff; padding:5px 14px; border-radius:20px; font-weight:800; font-size:.85rem; box-shadow: 0 2px 8px rgba(0,200,68,0.4); }
      .badge-sell { background:#ee0000; color:#ffffff; padding:5px 14px; border-radius:20px; font-weight:800; font-size:.85rem; box-shadow: 0 2px 8px rgba(238,0,0,0.4); }
      .badge-hold { background:#ff9900; color:#000000; padding:5px 14px; border-radius:20px; font-weight:800; font-size:.85rem; box-shadow: 0 2px 8px rgba(255,153,0,0.4); }
      
      /* Info boxes - bright with strong border */
      [data-testid="stAlert"] {
          background-color: #e8f4ff !important;
          border: 3px solid #0088ff !important;
          border-radius: 12px;
          color: #000000 !important;
      }
      [data-testid="stAlert"] p { color: #000000 !important; }
      
      /* Expander - blue header */
      .streamlit-expanderHeader, details summary { 
          background-color: #d0e4f7 !important;
          color: #003366 !important;
          border-radius: 10px;
          border: 2px solid #0066cc;
          font-weight: 700;
      }
      details summary span { color: #003366 !important; font-weight: 700; }
      details[open] { 
          background-color: #f0f7ff !important;
          border-radius: 10px;
      }
      
      /* Inputs - white with blue border */
      .stTextInput input, 
      .stNumberInput input,
      .stTextArea textarea,
      .stSelectbox [data-baseweb="select"] > div {
          background-color: #ffffff !important;
          color: #000000 !important;
          border: 3px solid #0088cc !important;
          border-radius: 10px;
          font-weight: 500;
      }
      .stTextInput input:focus, .stNumberInput input:focus {
          border-color: #0044aa !important;
          box-shadow: 0 0 0 4px rgba(0, 102, 204, 0.25) !important;
      }
      .stTextInput label,
      .stSelectbox label,
      .stNumberInput label {
          color: #003366 !important;
          font-weight: 700;
          font-size: 0.9rem;
      }
      
      /* Buttons - blue themed */
      .stButton button {
          background: #e8f0fa !important;
          color: #003366 !important;
          border: 3px solid #0066cc !important;
          border-radius: 10px;
          font-weight: 700;
          padding: 8px 16px;
      }
      .stButton button:hover {
          background: #0066cc !important;
          border-color: #004499 !important;
          color: #ffffff !important;
      }
      .stButton button[kind="primary"] {
          background: #0066cc !important;
          color: #ffffff !important;
          border: none !important;
          box-shadow: 0 4px 15px rgba(0, 102, 204, 0.4);
          font-weight: 800;
      }
      .stButton button[kind="primary"]:hover {
          background: #004499 !important;
      }
      
      /* Dividers - blue */
      hr { border-color: #0088cc !important; border-width: 3px; }
      
      /* Radio & Checkbox */
      .stRadio label, .stCheckbox label { color: #000000 !important; font-weight: 600; }
      .stRadio [data-baseweb="radio"] span { color: #000000 !important; }
      
      /* Caption - dark gray */
      .stCaption, [data-testid="stCaptionContainer"] { color: #333333 !important; font-weight: 500; }
      
      /* Charts - white with border */
      [data-testid="stPlotlyChart"] { 
          background-color: #ffffff !important;
          border-radius: 12px;
          border: 2px solid #0088cc;
          padding: 10px;
      }
      
      /* Success/Error messages */
      .stSuccess { background-color: #d4f5d4 !important; border: 2px solid #00aa00 !important; }
      .stError { background-color: #ffd4d4 !important; border: 2px solid #dd0000 !important; }
      .stWarning { background-color: #fff0c4 !important; border: 2px solid #cc9900 !important; }
      
      /* Columns */
      [data-testid="column"] { background-color: transparent !important; }
      
      /* Markdown text ensure black */
      .stMarkdown p, .stMarkdown span, .stMarkdown li { color: #000000 !important; }
      .stMarkdown strong { color: #003366 !important; }
      .stMarkdown code { background-color: #e8f0fa !important; color: #003366 !important; }
      
      /* ════════════════════════════════════════════════════════════════════════
         RESPONSIVE - Tablet & Mobile optimizations (Light Mode)
         ════════════════════════════════════════════════════════════════════════ */
      
      /* Tablet breakpoint */
      @media (max-width: 1024px) {
          .main .block-container { padding: 1rem 1.5rem !important; }
          div[data-testid="metric-container"] { padding: 15px 18px; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1.4rem; }
          h1 { font-size: 1.6rem !important; }
          h2 { font-size: 1.3rem !important; }
          button[data-baseweb="tab"] { font-size: 0.8rem; padding: 8px 10px; }
      }
      
      /* Mobile breakpoint */
      @media (max-width: 768px) {
          /* Reduce padding */
          .main .block-container { padding: 0.5rem 0.8rem !important; }
          
          /* Stack columns vertically */
          [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
          
          /* Metric cards - full width, smaller text */
          div[data-testid="metric-container"] { 
              padding: 12px 14px; 
              margin-bottom: 8px;
              border-width: 2px !important;
          }
          div[data-testid="metric-container"] label { font-size: 0.7rem; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1.2rem; }
          
          /* Headers smaller */
          h1 { font-size: 1.4rem !important; }
          h2 { font-size: 1.2rem !important; }
          h3 { font-size: 1rem !important; }
          
          /* Tabs - scrollable, smaller */
          [data-baseweb="tab-list"] { 
              overflow-x: auto; 
              -webkit-overflow-scrolling: touch;
              padding: 4px;
          }
          button[data-baseweb="tab"] { 
              font-size: 0.7rem; 
              padding: 6px 8px; 
              white-space: nowrap;
              min-width: auto;
          }
          
          /* Sidebar - hide or minimize */
          section[data-testid="stSidebar"] { 
              width: 260px !important; 
          }
          
          /* Buttons - full width on mobile */
          .stButton button { 
              width: 100% !important; 
              padding: 12px !important;
              font-size: 0.9rem;
          }
          
          /* DataFrames - scroll horizontally */
          .stDataFrame { overflow-x: auto !important; }
          
          /* Charts - reduce height */
          [data-testid="stPlotlyChart"] { min-height: 250px !important; }
          
          /* Info boxes */
          [data-testid="stAlert"] { 
              padding: 10px !important; 
              font-size: 0.85rem;
          }
          
          /* Input fields - larger touch targets */
          .stTextInput input, 
          .stNumberInput input,
          .stSelectbox [data-baseweb="select"] > div {
              min-height: 44px !important;
              font-size: 16px !important; /* Prevents zoom on iOS */
          }
      }
      
      /* Small mobile */
      @media (max-width: 480px) {
          .main .block-container { padding: 0.3rem 0.5rem !important; }
          h1 { font-size: 1.2rem !important; }
          h2 { font-size: 1.05rem !important; }
          div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 1rem; }
          button[data-baseweb="tab"] { font-size: 0.65rem; padding: 5px 6px; }
      }
    </style>
    """

st.markdown(theme_css, unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Download OHLCV data and compute full technical indicators."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        if df.empty:
            return pd.DataFrame()
        close = df["Close"].squeeze()
        # Moving Averages
        df["SMA20"] = ta_lib.trend.sma_indicator(close, window=20)
        df["SMA50"] = ta_lib.trend.sma_indicator(close, window=50)
        df["SMA200"] = ta_lib.trend.sma_indicator(close, window=200)
        df["EMA12"] = ta_lib.trend.ema_indicator(close, window=12)
        df["EMA26"] = ta_lib.trend.ema_indicator(close, window=26)
        # RSI
        df["RSI"] = ta_lib.momentum.rsi(close, window=14)
        # MACD
        df["MACD"] = ta_lib.trend.macd(close)
        df["MACD_Signal"] = ta_lib.trend.macd_signal(close)
        df["MACD_Hist"] = ta_lib.trend.macd_diff(close)
        # Bollinger Bands
        df["BB_Upper"] = ta_lib.volatility.bollinger_hband(close, window=20)
        df["BB_Lower"] = ta_lib.volatility.bollinger_lband(close, window=20)
        df["BB_Mid"] = ta_lib.volatility.bollinger_mavg(close, window=20)
        # ATR (Average True Range) for volatility
        df["ATR"] = ta_lib.volatility.average_true_range(df["High"].squeeze(), df["Low"].squeeze(), close, window=14)
        # Daily change
        df["Change_Pct"] = close.pct_change() * 100
        return df
    except Exception as e:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_current_price(ticker: str) -> float:
    """Return the latest closing price."""
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.last_price)
        return price if price > 0 else 0.0
    except Exception:
        return 0.0


# ── YouTube Video Analysis ────────────────────────────────────────────────────
# Complete list of popular stock tickers to detect
KNOWN_TICKERS = {
    # Mega caps & popular
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK.A", "BRK.B",
    "JPM", "JNJ", "V", "UNH", "HD", "PG", "MA", "DIS", "PYPL", "NFLX", "ADBE", "CRM",
    "INTC", "AMD", "CSCO", "PEP", "KO", "NKE", "MCD", "WMT", "COST", "ABBV", "MRK",
    "PFE", "TMO", "AVGO", "ACN", "LLY", "ORCL", "TXN", "QCOM", "UPS", "HON", "IBM",
    "GE", "CAT", "BA", "MMM", "GS", "AXP", "MS", "BLK", "SCHW", "C", "WFC", "BAC",
    # Growth & tech
    "PLTR", "SNOW", "CRWD", "ZS", "DDOG", "NET", "PANW", "OKTA", "MDB", "U", "RBLX",
    "COIN", "HOOD", "SOFI", "AFRM", "UPST", "SQ", "SHOP", "MELI", "SE", "BABA", "JD",
    "PDD", "NIO", "XPEV", "LI", "RIVN", "LCID", "F", "GM", "TM", "UBER", "LYFT",
    # AI & Semiconductors
    "ARM", "SMCI", "MRVL", "MU", "LRCX", "KLAC", "AMAT", "ASML", "TSM", "ON", "ADI",
    # Energy & commodities
    "XOM", "CVX", "COP", "SLB", "OXY", "DVN", "HAL", "BP", "SHEL", "TTE",
    # Healthcare & biotech
    "MRNA", "BNTX", "REGN", "VRTX", "GILD", "BIIB", "ILMN", "DXCM", "ISRG", "ZTS",
    # Financials & REITs
    "BX", "KKR", "APO", "SPGI", "MCO", "ICE", "CME", "NDAQ", "AMT", "PLD", "EQIX",
    # Consumer & retail
    "LULU", "TGT", "LOW", "TJX", "ROST", "DG", "DLTR", "SBUX", "CMG", "YUM", "QSR",
    # Misc popular
    "SPOT", "ZM", "DOCU", "TWLO", "TTD", "ROKU", "ABNB", "DASH", "DKNG", "PENN",
    "GME", "AMC", "BB", "BBBY", "SPCE", "PLUG", "FCEL", "BLNK", "CHPT", "QS",
    # Crypto related
    "MSTR", "MARA", "RIOT", "CLSK", "HUT", "BITF", "HIVE",
    # ETFs (popular ones)
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "ARKK", "ARKG", "XLF", "XLE", "XLK",
}

# Company name to ticker mapping
COMPANY_TO_TICKER = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "nvidia": "NVDA", "meta": "META", "facebook": "META",
    "tesla": "TSLA", "netflix": "NFLX", "disney": "DIS", "paypal": "PYPL",
    "adobe": "ADBE", "salesforce": "CRM", "intel": "INTC", "amd": "AMD",
    "cisco": "CSCO", "pepsi": "PEP", "pepsico": "PEP", "coca cola": "KO",
    "coke": "KO", "nike": "NKE", "mcdonald": "MCD", "mcdonalds": "MCD",
    "walmart": "WMT", "costco": "COST", "palantir": "PLTR", "snowflake": "SNOW",
    "crowdstrike": "CRWD", "cloudflare": "NET", "coinbase": "COIN", "robinhood": "HOOD",
    "sofi": "SOFI", "shopify": "SHOP", "alibaba": "BABA", "nio": "NIO",
    "rivian": "RIVN", "lucid": "LCID", "ford": "F", "uber": "UBER",
    "supermicro": "SMCI", "super micro": "SMCI", "micron": "MU", "broadcom": "AVGO",
    "exxon": "XOM", "chevron": "CVX", "moderna": "MRNA", "pfizer": "PFE",
    "johnson and johnson": "JNJ", "j&j": "JNJ", "berkshire": "BRK.B",
    "jp morgan": "JPM", "jpmorgan": "JPM", "goldman sachs": "GS", "goldman": "GS",
    "morgan stanley": "MS", "blackrock": "BLK", "visa": "V", "mastercard": "MA",
    "american express": "AXP", "amex": "AXP", "bank of america": "BAC",
    "wells fargo": "WFC", "citigroup": "C", "citi": "C", "gamestop": "GME",
    "amc": "AMC", "starbucks": "SBUX", "chipotle": "CMG", "spotify": "SPOT",
    "zoom": "ZM", "docusign": "DOCU", "airbnb": "ABNB", "doordash": "DASH",
    "draftkings": "DKNG", "microstrategy": "MSTR", "arm": "ARM", "arm holdings": "ARM",
    "oracle": "ORCL", "ibm": "IBM", "boeing": "BA", "caterpillar": "CAT",
    "general electric": "GE", "home depot": "HD", "target": "TGT", "lowes": "LOW",
    "lowe's": "LOW", "best buy": "BBY", "roku": "ROKU", "datadog": "DDOG",
    "mongodb": "MDB", "unity": "U", "roblox": "RBLX", "twilio": "TWLO",
    "asml": "ASML", "taiwan semiconductor": "TSM", "tsmc": "TSM",
    "spy": "SPY", "qqq": "QQQ", "ark": "ARKK", "s&p 500": "SPY", "s&p": "SPY",
    "nasdaq": "QQQ", "dow jones": "DIA", "dow": "DIA",
}


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'  # Just the ID
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_youtube_transcript(video_id: str) -> tuple[str, str]:
    """Fetch YouTube video transcript. Returns (transcript_text, error_message)."""
    try:
        # New API: create instance and use fetch method
        api = YouTubeTranscriptApi()
        
        # Try to list available transcripts first
        try:
            transcript_list = api.list(video_id)
            
            # Find best transcript (prefer English, then any)
            best_transcript = None
            preferred_langs = ['en', 'en-US', 'en-GB', 'nl', 'de', 'fr']
            
            for transcript in transcript_list:
                if transcript.language_code in preferred_langs:
                    best_transcript = transcript
                    break
            
            # If no preferred language found, use first available
            if not best_transcript and transcript_list:
                best_transcript = transcript_list[0]
            
            if best_transcript:
                # Fetch the transcript content
                fetched = best_transcript.fetch()
                full_text = " ".join([snippet.text for snippet in fetched])
                return full_text, ""
        except Exception:
            pass
        
        # Fallback: direct fetch (auto-selects best transcript)
        fetched = api.fetch(video_id)
        full_text = " ".join([snippet.text for snippet in fetched])
        return full_text, ""
        
    except TranscriptsDisabled:
        return "", "Transcripts zijn uitgeschakeld voor deze video"
    except NoTranscriptFound:
        return "", "Geen transcript gevonden voor deze video"
    except Exception as e:
        return "", f"Fout bij ophalen transcript: {str(e)}"


def detect_stocks_in_text(text: str) -> list[dict]:
    """Detect stock tickers and company names in text, with sentiment analysis."""
    detected = []
    text_lower = text.lower()
    text_upper = text.upper()
    
    # 1. Direct ticker detection (e.g., "AAPL", "$TSLA", "ticker: NVDA")
    # Pattern matches standalone tickers (with optional $ prefix)
    ticker_patterns = [
        r'\$([A-Z]{2,5})\b',  # $AAPL
        r'\b([A-Z]{2,5})\s+stock\b',  # AAPL stock
        r'\bstock\s+([A-Z]{2,5})\b',  # stock AAPL
        r'\bticker[:\s]+([A-Z]{2,5})\b',  # ticker: AAPL
        r'\b([A-Z]{2,5})\s+shares\b',  # AAPL shares
    ]
    
    found_tickers = set()
    
    for pattern in ticker_patterns:
        matches = re.findall(pattern, text_upper, re.IGNORECASE)
        for match in matches:
            ticker = match.upper()
            if ticker in KNOWN_TICKERS and ticker not in found_tickers:
                found_tickers.add(ticker)
    
    # Also check for standalone tickers mentioned multiple times
    words = re.findall(r'\b([A-Z]{2,5})\b', text_upper)
    word_counts = {}
    for word in words:
        if word in KNOWN_TICKERS:
            word_counts[word] = word_counts.get(word, 0) + 1
    
    # Add tickers mentioned 2+ times
    for ticker, count in word_counts.items():
        if count >= 2 and ticker not in found_tickers:
            found_tickers.add(ticker)
    
    # 2. Company name detection
    for company, ticker in COMPANY_TO_TICKER.items():
        if company in text_lower and ticker not in found_tickers:
            # Check it's mentioned as a word (not part of another word)
            pattern = r'\b' + re.escape(company) + r'\b'
            if re.search(pattern, text_lower):
                found_tickers.add(ticker)
    
    # 3. Sentiment analysis per ticker
    # Find context around each ticker mention with IMPROVED detection
    
    # Weighted sentiment words (stronger words = higher weight)
    # Optimized for finance YouTuber language (Meet Kevin, Graham Stephan, Jeremy, etc.)
    bullish_signals = {
        # Very strong bullish (weight 3)
        'bought heavy': 3, 'buying heavy': 3, 'loading up': 3, 'loaded up': 3,
        'adding aggressively': 3, 'very bullish': 3, 'extremely bullish': 3,
        'my favorite': 3, 'top pick': 3, 'strong buy': 3, 'must buy': 3,
        'no brainer': 3, 'slam dunk': 3, 'huge opportunity': 3,
        'always a buy': 3, 'generational buy': 3, 'life changing': 3,
        'doubled down': 3, 'tripled down': 3, 'all in': 3,
        'best stock': 3, 'best investment': 3, 'can\'t go wrong': 3,
        # Strong bullish (weight 2)
        'bought': 2, 'buying': 2, 'added': 2, 'adding': 2, 'accumulating': 2,
        'bullish': 2, 'long term buy': 2, 'great opportunity': 2, 'undervalued': 2,
        'love this': 2, 'love the': 2, 'i love': 2, 'really like': 2,
        'going higher': 2, 'will recover': 2, 'bottom is in': 2, 'bottoming': 2,
        'cheap': 2, 'discount': 2, 'on sale': 2, 'steal': 2,
        'picked up': 2, 'scooped up': 2, 'grabbed': 2, 'snapped up': 2,
        'backing up the truck': 2, 'fire sale': 2, 'blood in the streets': 2,
        'long term hold': 2, 'hold forever': 2, 'never sell': 2,
        'conviction': 2, 'high conviction': 2, 'confident': 2,
        # Moderate bullish (weight 1)
        'buy': 1, 'long': 1, 'calls': 1, 'upside': 1, 'growth': 1,
        'opportunity': 1, 'potential': 1, 'promising': 1, 'strong': 1,
        'rally': 1, 'breakout': 1, 'recovery': 1, 'rebound': 1,
        'like': 1, 'positive': 1, 'optimistic': 1, 'excited': 1,
        'oversold': 1, 'beaten down': 2, 'pullback': 1, 'dip': 1,
    }
    
    bearish_signals = {
        # Very strong bearish (weight 3)
        'sold everything': 3, 'selling everything': 3, 'avoid at all costs': 3,
        'very bearish': 3, 'extremely bearish': 3, 'stay away': 3,
        'disaster': 3, 'terrible': 3, 'worst': 3,
        'would never buy': 3, 'dead money': 3, 'going to zero': 3,
        # Strong bearish (weight 2)
        'sold': 2, 'selling': 2, 'reduced': 2, 'trimmed': 2,
        'bearish': 2, 'overvalued': 2, 'too expensive': 2, 'bubble': 2,
        'avoid': 2, 'stay away': 2, 'concerned': 2, 'worried': 2,
        'going lower': 2, 'will crash': 2, 'will drop': 2,
        # Moderate bearish (weight 1)
        'sell': 1, 'short': 1, 'puts': 1, 'downside': 1, 'risk': 1,
        'weak': 1, 'struggling': 1, 'problems': 1, 'issues': 1,
        'dump': 1, 'drop': 1, 'crash': 1, 'plunge': 1,
        'negative': 1, 'pessimistic': 1, 'cautious': 1,
    }
    
    for ticker in found_tickers:
        # Find ALL context windows around ticker mentions (larger window: 300 chars)
        ticker_positions = [m.start() for m in re.finditer(r'\b' + ticker + r'\b', text_upper)]
        
        # Also find positions of company name if applicable
        company_name = None
        for company, t in COMPANY_TO_TICKER.items():
            if t == ticker:
                company_name = company
                break
        
        if company_name:
            company_positions = [m.start() for m in re.finditer(r'\b' + re.escape(company_name) + r'\b', text_lower)]
            ticker_positions.extend(company_positions)
        
        total_bullish_score = 0
        total_bearish_score = 0
        best_context = ""
        
        for pos in ticker_positions:
            start = max(0, pos - 300)
            end = min(len(text_lower), pos + 300)
            context = text_lower[start:end]
            
            # Calculate weighted scores
            bullish_score = sum(weight for phrase, weight in bullish_signals.items() if phrase in context)
            bearish_score = sum(weight for phrase, weight in bearish_signals.items() if phrase in context)
            
            total_bullish_score += bullish_score
            total_bearish_score += bearish_score
            
            # Keep best context (one with most signal)
            if bullish_score + bearish_score > 0:
                best_context = context
        
        # Determine sentiment based on total weighted scores
        sentiment = "🟡 Neutral"
        if total_bullish_score > total_bearish_score + 1:  # Need clear advantage
            sentiment = "🟢 Bullish"
        elif total_bearish_score > total_bullish_score + 1:
            sentiment = "🔴 Bearish"
        
        # Get current price
        current_price = get_current_price(ticker)
        
        # Clean up context for display
        if best_context:
            # Find the most relevant sentence
            sentences = best_context.split('.')
            relevant_sentence = max(sentences, key=len) if sentences else best_context
            context_display = relevant_sentence.strip()[:150]
        else:
            context_display = ""
        
        detected.append({
            "ticker": ticker,
            "sentiment": sentiment,
            "price": current_price,
            "context": context_display + "..." if context_display else "",
            "bullish_score": total_bullish_score,
            "bearish_score": total_bearish_score,
        })
    
    # Sort by ticker
    detected = sorted(detected, key=lambda x: x["ticker"])
    
    return detected


def calculate_fair_value(df: pd.DataFrame, current_price: float, ticker: str) -> dict:
    """Professional Fair Value calculation with reliability checks."""
    if df.empty or current_price == 0:
        return {"fair_value": current_price, "upside": 0, "valuation": "N/A", "reliability": "⚠️ No Data"}
    
    close = df["Close"]
    
    # ════════════════════════════════════════════════════════════════════════════
    # DATA QUALITY CHECKS
    # ════════════════════════════════════════════════════════════════════════════
    
    # Check 1: Minimum data points
    if len(df) < 50:
        return {"fair_value": current_price, "upside": 0, "valuation": "🟡 Fair Value", "reliability": "⚠️ Insufficient Data (<50 days)"}
    
    # Check 2: Extreme price movements (potential delisting, stock split, or error)
    price_change_1d = abs((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(df) >= 2 else 0
    if price_change_1d > 50:
        return {"fair_value": current_price, "upside": 0, "valuation": "🔴 CAUTION", "reliability": f"⚠️ Extreme move ({price_change_1d:.1f}%)"}
    
    # Check 3: Very low price stocks (penny stocks - unreliable)
    if current_price < 1:
        return {"fair_value": current_price, "upside": 0, "valuation": "🔴 CAUTION", "reliability": "⚠️ Penny Stock (unreliable)"}
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 1: TECHNICAL SUPPORT/RESISTANCE
    # ════════════════════════════════════════════════════════════════════════════
    weeks_52_high = close.tail(252).max() if len(df) >= 252 else close.max()
    weeks_52_low = close.tail(252).min() if len(df) >= 252 else close.min()
    
    technical_fair = (weeks_52_high + weeks_52_low) / 2
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 2: MOMENTUM-BASED (Mean Reversion)
    # ════════════════════════════════════════════════════════════════════════════
    sma50 = ta_lib.trend.sma_indicator(close, window=50).iloc[-1] if len(df) >= 50 else float("nan")
    sma200 = ta_lib.trend.sma_indicator(close, window=200).iloc[-1] if len(df) >= 200 else float("nan")
    
    std_dev = close.rolling(window=20).std().iloc[-1] if len(df) >= 20 else float("nan")
    
    momentum_fair = sma200 if not pd.isna(sma200) else current_price
    if not pd.isna(std_dev) and std_dev > 0:
        momentum_fair = momentum_fair * (1 + (std_dev / momentum_fair) * 0.1)
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 3: RELATIVE STRENGTH (Using historical average + RSI)
    # ════════════════════════════════════════════════════════════════════════════
    rsi = ta_lib.momentum.rsi(close, window=14).iloc[-1] if len(df) >= 14 else 50
    
    hist_avg = close.tail(252).mean() if len(df) >= 252 else close.mean()
    
    rsi_factor = (rsi - 50) / 100
    relative_fair = hist_avg * (1 - rsi_factor * 0.2)
    
    # ════════════════════════════════════════════════════════════════════════════
    # OUTLIER DETECTION - Filter extreme valuations
    # ════════════════════════════════════════════════════════════════════════════
    fair_values = []
    weights = []
    
    if not pd.isna(technical_fair) and technical_fair > 0:
        fair_values.append(technical_fair)
        weights.append(0.33)
    
    if not pd.isna(momentum_fair) and momentum_fair > 0:
        fair_values.append(momentum_fair)
        weights.append(0.34)
    
    if not pd.isna(relative_fair) and relative_fair > 0:
        fair_values.append(relative_fair)
        weights.append(0.33)
    
    if fair_values and sum(weights) > 0:
        fair_value = sum(v * w for v, w in zip(fair_values, weights)) / sum(weights)
    else:
        fair_value = current_price
    
    # ════════════════════════════════════════════════════════════════════════════
    # RELIABILITY ASSESSMENT
    # ════════════════════════════════════════════════════════════════════════════
    
    upside_pct = ((fair_value - current_price) / current_price * 100) if current_price > 0 else 0
    
    # Flag extreme divergences
    if abs(upside_pct) > 50:
        reliability = "⚠️ EXTREME - Verify Data"
    elif abs(upside_pct) > 30:
        reliability = "⚠️ High Uncertainty"
    elif abs(upside_pct) > 15:
        reliability = "✅ Moderate Confidence"
    else:
        reliability = "✅ Good Confidence"
    
    # ════════════════════════════════════════════════════════════════════════════
    # VALUATION ASSESSMENT (Conservative)
    # ════════════════════════════════════════════════════════════════════════════
    
    if upside_pct > 25:
        valuation = "🟢 STRONG BUY (+{:.1f}%)".format(upside_pct)
    elif upside_pct > 12:
        valuation = "🟢 Undervalued (+{:.1f}%)".format(upside_pct)
    elif upside_pct > -12:
        valuation = "🟡 Fair Value (±{:.1f}%)".format(abs(upside_pct))
    elif upside_pct > -25:
        valuation = "🔴 Overvalued ({:.1f}%)".format(upside_pct)
    else:
        valuation = "🔴 STRONG SELL ({:.1f}%)".format(upside_pct)
    
    return {
        "fair_value": fair_value,
        "upside": upside_pct,
        "valuation": valuation,
        "current_price": current_price,
        "reliability": reliability,
        "methods": {
            "technical": technical_fair,
            "momentum": momentum_fair,
            "relative": relative_fair,
        }
    }


def classify_status(price: float, sma200: float, rsi: float, df: pd.DataFrame, fair_value: float) -> str:
    """Trading signal based on Fair Value + RSI confirmation."""
    if pd.isna(price) or price == 0 or df.empty:
        return "HOLD"
    
    if pd.isna(rsi) or pd.isna(fair_value):
        return "HOLD"
    
    upside_pct = ((fair_value - price) / price * 100) if price > 0 else 0
    
    # BUY: Undervalued + Supportive RSI (not overbought)
    if upside_pct > 12 and rsi < 70:
        return "BUY"
    
    # SELL: Overvalued + Weak RSI (not oversold)
    elif upside_pct < -12 and rsi > 30:
        return "SELL"
    
    # HOLD: Everything else
    else:
        return "HOLD"


def status_badge(status: str) -> str:
    css = {"BUY": "badge-buy", "SELL": "badge-sell", "HOLD": "badge-hold"}
    return f'<span class="{css.get(status, "badge-hold")}">{status}</span>'


@st.cache_data(ttl=3600)
def fetch_ticker_info(ticker: str) -> dict:
    """Fetch detailed ticker information with fundamentals."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        # Calculate some derived metrics
        pe_ratio = info.get("trailingPE", None)
        forward_pe = info.get("forwardPE", None)
        pb_ratio = info.get("priceToBook", None)
        
        # Earnings growth (YoY)
        earnings_growth = info.get("earningsGrowth", None)
        
        # Debt/Equity
        total_debt = info.get("totalDebt", None)
        total_equity = info.get("totalEquity", None)
        debt_to_equity = None
        if total_debt and total_equity and total_equity > 0:
            debt_to_equity = total_debt / total_equity
        
        # Profit margins
        profit_margin = info.get("profitMargins", None)
        operating_margin = info.get("operatingMargins", None)
        
        # Return on Equity (ROE)
        roe = info.get("returnOnEquity", None)
        
        # Revenue/Cash flow
        revenue_growth = info.get("revenueGrowth", None)
        free_cash_flow = info.get("operatingCashflow", None)
        
        return {
            "dividend_yield": info.get("dividendYield", 0),
            "pe_ratio": pe_ratio,
            "forward_pe": forward_pe,
            "pb_ratio": pb_ratio,
            "market_cap": info.get("marketCap", None),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "52_week_high": info.get("fiftyTwoWeekHigh", None),
            "52_week_low": info.get("fiftyTwoWeekLow", None),
            # Fundamentals
            "earnings_growth": earnings_growth,
            "revenue_growth": revenue_growth,
            "profit_margin": profit_margin,
            "operating_margin": operating_margin,
            "roe": roe,
            "debt_to_equity": debt_to_equity,
            "free_cash_flow": free_cash_flow,
        }
    except Exception:
        return {
            "dividend_yield": 0,
            "pe_ratio": None,
            "forward_pe": None,
            "pb_ratio": None,
            "market_cap": None,
            "sector": "N/A",
            "industry": "N/A",
            "52_week_high": None,
            "52_week_low": None,
            "earnings_growth": None,
            "revenue_growth": None,
            "profit_margin": None,
            "operating_margin": None,
            "roe": None,
            "debt_to_equity": None,
            "free_cash_flow": None,
        }


def calculate_support_resistance(df: pd.DataFrame) -> dict:
    """Calculate support and resistance levels."""
    if df.empty or len(df) < 50:
        return {"support": None, "resistance": None, "pivot": None}
    
    close = df["Close"]
    
    # Pivot Point Method
    high = df["High"].iloc[-1]
    low = df["Low"].iloc[-1]
    close_price = df["Close"].iloc[-1]
    
    pivot = (high + low + close_price) / 3
    support1 = (2 * pivot) - high
    resistance1 = (2 * pivot) - low
    
    return {
        "pivot": pivot,
        "support1": support1,
        "resistance1": resistance1,
        "support2": pivot - (resistance1 - support1),
        "resistance2": pivot + (resistance1 - support1),
    }


def calculate_volume_trend(df: pd.DataFrame) -> str:
    """Analyze volume trend."""
    if df.empty or len(df) < 20:
        return "N/A"
    
    recent_volume = df["Volume"].tail(5).mean()
    historical_volume = df["Volume"].tail(20).mean()
    
    if recent_volume > historical_volume * 1.5:
        return "📈 High Volume (Bullish)"
    elif recent_volume < historical_volume * 0.7:
        return "📉 Low Volume (Weak)"
    else:
        return "📊 Normal Volume"


def get_market_health() -> dict:
    """Get market health indicators (VIX-like, market breadth)."""
    try:
        # VIX index (volatility)
        vix = yf.Ticker("^VIX")
        vix_data = vix.history(period="1y")
        if not vix_data.empty:
            current_vix = vix_data["Close"].iloc[-1]
            vix_20d_avg = vix_data["Close"].tail(20).mean()
            
            if current_vix < 15:
                market_regime = "🟢 Calm (Low volatility)"
            elif current_vix < 20:
                market_regime = "🟡 Normal (Moderate volatility)"
            elif current_vix < 30:
                market_regime = "🔴 Stressed (High volatility)"
            else:
                market_regime = "⚠️ Panic (Extreme volatility)"
        else:
            current_vix = float("nan")
            vix_20d_avg = float("nan")
            market_regime = "N/A"
        
        # S&P 500 trend (bull/bear indicator)
        sp500 = yf.Ticker("^GSPC")
        sp500_data = sp500.history(period="1y")
        if not sp500_data.empty:
            sp500_price = sp500_data["Close"].iloc[-1]
            sp500_sma200 = sp500_data["Close"].tail(200).mean() if len(sp500_data) >= 200 else float("nan")
            
            if not pd.isna(sp500_sma200) and sp500_price > sp500_sma200:
                bull_regime = "🟢 Bull Market"
            else:
                bull_regime = "🔴 Bear Market"
        else:
            sp500_price = float("nan")
            sp500_sma200 = float("nan")
            bull_regime = "N/A"
        
        return {
            "vix": current_vix,
            "vix_20d_avg": vix_20d_avg,
            "market_regime": market_regime,
            "bull_regime": bull_regime,
            "sp500_price": sp500_price,
            "sp500_sma200": sp500_sma200,
        }
    except Exception:
        return {
            "vix": float("nan"),
            "vix_20d_avg": float("nan"),
            "market_regime": "N/A",
            "bull_regime": "N/A",
            "sp500_price": float("nan"),
            "sp500_sma200": float("nan"),
        }


# ════════════════════════════════════════════════════════════════════════════
# NEW FEATURES: Price Alerts, Portfolio History, News, Export, etc.
# ════════════════════════════════════════════════════════════════════════════

def check_price_alerts(alerts: list, ticker_data: dict) -> list:
    """Check which price alerts have been triggered."""
    triggered = []
    for alert in alerts:
        ticker = alert.get("ticker", "")
        target_price = alert.get("target_price", 0)
        alert_type = alert.get("type", "above")  # "above" or "below"
        
        df = ticker_data.get(ticker, pd.DataFrame())
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        
        if alert_type == "above" and current_price >= target_price:
            triggered.append({
                "ticker": ticker,
                "target": target_price,
                "current": current_price,
                "type": "🟢 Above Target",
                "message": f"{ticker} has reached ${current_price:.2f} (target: ${target_price:.2f})"
            })
        elif alert_type == "below" and current_price <= target_price:
            triggered.append({
                "ticker": ticker,
                "target": target_price,
                "current": current_price,
                "type": "🔴 Below Target",
                "message": f"{ticker} has dropped to ${current_price:.2f} (target: ${target_price:.2f})"
            })
    
    return triggered


def record_portfolio_snapshot(portfolio_positions, ticker_data: dict, existing_history: list) -> list:
    """Record daily portfolio value snapshot for history tracking."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Check if we already have today's snapshot
    if existing_history and existing_history[-1].get("date") == today:
        return existing_history  # Already recorded today
    
    total_value = 0
    total_cost = 0
    
    for pos in portfolio_positions:
        df = ticker_data.get(pos["ticker"], pd.DataFrame())
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            total_value += pos["qty"] * current_price
            total_cost += pos["qty"] * pos["gak"]
    
    if total_value > 0:
        existing_history.append({
            "date": today,
            "value": total_value,
            "cost": total_cost,
            "return_pct": ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
        })
    
    # Keep only last 365 days
    if len(existing_history) > 365:
        existing_history = existing_history[-365:]
    
    return existing_history


@st.cache_data(ttl=3600)
def fetch_stock_news(ticker: str) -> list:
    """Fetch recent news for a stock."""
    try:
        t = yf.Ticker(ticker)
        news = t.news
        if news:
            # Return top 5 news items
            return [{
                "title": n.get("title", "No title"),
                "publisher": n.get("publisher", "Unknown"),
                "link": n.get("link", "#"),
                "published": datetime.fromtimestamp(n.get("providerPublishTime", 0)).strftime("%d %b %Y") if n.get("providerPublishTime") else "Unknown",
                "type": n.get("type", "STORY")
            } for n in news[:5]]
        return []
    except Exception:
        return []


@st.cache_data(ttl=3600)
def get_earnings_calendar(ticker: str) -> dict:
    """Get upcoming earnings date for a stock."""
    try:
        t = yf.Ticker(ticker)
        calendar = t.calendar
        
        if calendar is not None and not calendar.empty:
            # calendar can be DataFrame or dict
            if isinstance(calendar, pd.DataFrame):
                earnings_date = calendar.iloc[0, 0] if calendar.shape[1] > 0 else None
            else:
                earnings_date = calendar.get("Earnings Date", [None])[0]
            
            if earnings_date:
                if isinstance(earnings_date, str):
                    return {"earnings_date": earnings_date, "days_until": "Unknown"}
                else:
                    days_until = (earnings_date - datetime.now()).days
                    return {
                        "earnings_date": earnings_date.strftime("%Y-%m-%d"),
                        "days_until": days_until
                    }
        return {"earnings_date": None, "days_until": None}
    except Exception:
        return {"earnings_date": None, "days_until": None}


def export_portfolio_csv(portfolio_positions, ticker_data: dict) -> str:
    """Export portfolio to CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Ticker", "Quantity", "Purchase Price", "Current Price", "Current Value", "P/L ($)", "P/L (%)", "Sector"])
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        info = fetch_ticker_info(ticker)
        
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            current_value = pos["qty"] * current_price
            cost = pos["qty"] * pos["gak"]
            pl = current_value - cost
            pl_pct = (pl / cost * 100) if cost > 0 else 0
            
            writer.writerow([
                ticker,
                pos["qty"],
                f"${pos['gak']:.2f}",
                f"${current_price:.2f}",
                f"${current_value:.2f}",
                f"${pl:.2f}",
                f"{pl_pct:.2f}%",
                info.get("sector", "Unknown")
            ])
    
    return output.getvalue()


def calculate_position_size(account_value: float, risk_per_trade: float, entry_price: float, stop_loss: float) -> dict:
    """Calculate position size based on risk management."""
    if entry_price <= 0 or stop_loss <= 0:
        return {}
    
    # Risk amount
    risk_amount = account_value * (risk_per_trade / 100)
    
    # Price difference (risk per share)
    risk_per_share = abs(entry_price - stop_loss)
    
    if risk_per_share == 0:
        return {}
    
    # Number of shares
    shares = int(risk_amount / risk_per_share)
    
    # Total position value
    position_value = shares * entry_price
    
    # Position as % of portfolio
    position_pct = (position_value / account_value * 100) if account_value > 0 else 0
    
    # Kelly Criterion (simplified)
    # Assumes 50% win rate with 2:1 reward-to-risk
    win_rate = 0.50
    win_loss_ratio = 2.0
    kelly_pct = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
    kelly_shares = int((account_value * kelly_pct) / entry_price)
    
    return {
        "shares": shares,
        "position_value": position_value,
        "position_pct": position_pct,
        "risk_amount": risk_amount,
        "risk_per_share": risk_per_share,
        "kelly_shares": kelly_shares,
        "kelly_value": kelly_shares * entry_price,
    }


def build_enhanced_chart(df: pd.DataFrame, ticker: str, show_bb: bool = True, show_macd: bool = True, theme: str = "dark") -> go.Figure:
    """Enhanced candlestick chart with Bollinger Bands and MACD."""
    if df.empty:
        return go.Figure()
    
    close = df["Close"]
    
    # Calculate indicators
    if show_bb:
        bb_upper = ta_lib.volatility.bollinger_hband(close, window=20)
        bb_lower = ta_lib.volatility.bollinger_lband(close, window=20)
        bb_mid = ta_lib.volatility.bollinger_mavg(close, window=20)
    
    if show_macd:
        macd = ta_lib.trend.macd(close)
        macd_signal = ta_lib.trend.macd_signal(close)
        macd_hist = ta_lib.trend.macd_diff(close)
    
    # Create subplots
    n_rows = 2 + (1 if show_macd else 0)
    row_heights = [0.5, 0.25, 0.25] if show_macd else [0.6, 0.4]
    subplot_titles = [f"{ticker} - Price", "RSI (14)"]
    if show_macd:
        subplot_titles.append("MACD")
    
    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )
    
    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"],
            name="Price",
            increasing_line_color="#3fb950",
            decreasing_line_color="#f85149",
        ),
        row=1, col=1,
    )
    
    # SMA200
    if "SMA200" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["SMA200"], name="SMA 200",
                      line=dict(color="#58a6ff", width=1.5, dash="dot")),
            row=1, col=1,
        )
    
    # Bollinger Bands
    if show_bb:
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_upper, name="BB Upper",
                      line=dict(color="#bc8ef7", width=1), opacity=0.6),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_lower, name="BB Lower",
                      line=dict(color="#bc8ef7", width=1), opacity=0.6,
                      fill="tonexty", fillcolor="rgba(188,142,247,0.1)"),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_mid, name="BB Mid",
                      line=dict(color="#bc8ef7", width=1, dash="dash"), opacity=0.4),
            row=1, col=1,
        )
    
    # RSI
    if "RSI" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["RSI"], name="RSI",
                      line=dict(color="#e3b341", width=1.5),
                      fill="tozeroy", fillcolor="rgba(227,179,65,0.08)"),
            row=2, col=1,
        )
        fig.add_hline(y=70, line_dash="dash", line_color="#f85149", opacity=0.5, row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#3fb950", opacity=0.5, row=2, col=1)
    
    # MACD
    if show_macd:
        fig.add_trace(
            go.Scatter(x=df.index, y=macd, name="MACD",
                      line=dict(color="#58a6ff", width=1.5)),
            row=3, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=macd_signal, name="Signal",
                      line=dict(color="#f85149", width=1.5)),
            row=3, col=1,
        )
        colors = ["#3fb950" if v >= 0 else "#f85149" for v in macd_hist]
        fig.add_trace(
            go.Bar(x=df.index, y=macd_hist, name="Histogram",
                  marker_color=colors, opacity=0.6),
            row=3, col=1,
        )
    
    _chart_template = "plotly_dark" if st.session_state.get('theme', 'dark') == 'dark' else "plotly_white"
    _chart_bg = "#0e1117" if st.session_state.get('theme', 'dark') == 'dark' else "#ffffff"
    fig.update_layout(
        template=_chart_template,
        paper_bgcolor=_chart_bg,
        plot_bgcolor=_chart_bg,
        height=700 if show_macd else 550,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        showlegend=True,
    )
    fig.update_yaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    fig.update_xaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    
    return fig


# ════════════════════════════════════════════════════════════════════════════
# ADVANCED FEATURES FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

def calculate_risk_metrics(portfolio_positions, ticker_data: dict) -> dict:
    """Calculate advanced risk metrics: VaR, Sharpe, Max Drawdown, Beta."""
    if not portfolio_positions:
        return {}
    
    returns_list = []
    weights = []
    total_value = 0
    
    for pos in portfolio_positions:
        df = ticker_data.get(pos["ticker"], pd.DataFrame())
        if not df.empty and len(df) >= 60:
            position_value = pos["qty"] * df["Close"].iloc[-1]
            total_value += position_value
            weights.append(position_value)
            
            # Calculate returns
            returns = df["Close"].pct_change().dropna()
            if len(returns) > 0:
                returns_list.append(returns)
    
    if not returns_list or total_value == 0:
        return {}
    
    # Normalize weights
    weights = [w / total_value for w in weights]
    
    # Combine returns into portfolio
    combined_returns = pd.concat(returns_list, axis=1).fillna(0)
    portfolio_returns = (combined_returns * weights).sum(axis=1)
    
    # VaR (95% confidence, 1-day loss)
    var_95 = portfolio_returns.quantile(0.05)
    
    # Sharpe Ratio (annualized)
    annualized_return = portfolio_returns.mean() * 252
    annualized_std = portfolio_returns.std() * np.sqrt(252)
    risk_free_rate = 0.04  # Current risk-free rate
    sharpe = (annualized_return - risk_free_rate) / annualized_std if annualized_std > 0 else 0
    
    # Max Drawdown
    cumulative = (1 + portfolio_returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()
    
    # Beta (vs S&P 500)
    try:
        sp500 = yf.Ticker("^GSPC")
        sp500_data = sp500.history(period="1y", progress=False)
        sp500_returns = sp500_data["Close"].pct_change().dropna()
        
        if len(sp500_returns) == len(portfolio_returns):
            covariance = portfolio_returns.cov(sp500_returns)
            sp500_variance = sp500_returns.var()
            beta = covariance / sp500_variance if sp500_variance > 0 else 0
        else:
            beta = float("nan")
    except:
        beta = float("nan")
    
    return {
        "var_95": var_95,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "beta": beta,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_std,
    }


def get_position_alerts(portfolio_positions, ticker_data: dict) -> list:
    """Generate alerts for portfolio positions."""
    alerts = []
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        purchase_price = pos["gak"]
        current_return = ((current_price - purchase_price) / purchase_price * 100)
        
        # Alert 1: Large drop (>10%)
        if current_return < -10:
            alerts.append({
                "type": "⚠️ Major Loss",
                "ticker": ticker,
                "message": f"{ticker} down {current_return:.1f}% - Consider selling",
                "severity": "high" if current_return < -20 else "medium"
            })
        
        # Alert 2: Large gain (>30%)
        if current_return > 30:
            alerts.append({
                "type": "🎯 Take Profit",
                "ticker": ticker,
                "message": f"{ticker} up {current_return:.1f}% - Consider taking profit",
                "severity": "medium"
            })
        
        # Alert 3: RSI extremes
        rsi = latest(ticker, "RSI")
        if not pd.isna(rsi):
            if rsi > 75:
                alerts.append({
                    "type": "🔥 Overbought",
                    "ticker": ticker,
                    "message": f"{ticker} RSI at {rsi:.1f} - Potential pullback",
                    "severity": "low"
                })
            elif rsi < 25:
                alerts.append({
                    "type": "❄️ Oversold",
                    "ticker": ticker,
                    "message": f"{ticker} RSI at {rsi:.1f} - Potential bounce",
                    "severity": "low"
                })
    
    return sorted(alerts, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["severity"]])


def calculate_tax_loss_harvesting(portfolio_positions, ticker_data: dict) -> list:
    """Identify positions for tax loss harvesting."""
    tax_loss_positions = []
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        purchase_price = pos["gak"]
        total_loss = pos["qty"] * (current_price - purchase_price)
        loss_pct = ((current_price - purchase_price) / purchase_price * 100)
        
        # Only include if losing money
        if total_loss < 0:
            tax_loss_positions.append({
                "ticker": ticker,
                "current_price": current_price,
                "purchase_price": purchase_price,
                "total_loss": total_loss,
                "loss_pct": loss_pct,
                "shares": pos["qty"],
                "tax_benefit": total_loss * 0.24,  # Assume 24% tax bracket
            })
    
    return sorted(tax_loss_positions, key=lambda x: x["total_loss"])


def get_rebalancing_suggestion(portfolio_positions, ticker_data: dict, target_allocation: dict = None) -> dict:
    """Suggest portfolio rebalancing."""
    if not portfolio_positions:
        return {}
    
    # Default target: 60% stocks, 40% diversified
    if target_allocation is None:
        target_allocation = {"Tech": 0.25, "Finance": 0.20, "Healthcare": 0.15, "Other": 0.40}
    
    # Current allocation
    sector_values = {}
    total_value = 0
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        info = fetch_ticker_info(ticker)
        sector = info.get("sector", "Other")
        value = pos["qty"] * df["Close"].iloc[-1]
        
        total_value += value
        sector_values[sector] = sector_values.get(sector, 0) + value
    
    if total_value == 0:
        return {}
    
    # Calculate % allocation
    current_allocation = {k: v / total_value for k, v in sector_values.items()}
    
    # Compare to target
    rebalancing = []
    for sector, target_pct in target_allocation.items():
        current_pct = current_allocation.get(sector, 0)
        diff = current_pct - target_pct
        
        if abs(diff) > 0.05:  # Threshold 5%
            rebalancing.append({
                "sector": sector,
                "current": current_pct * 100,
                "target": target_pct * 100,
                "action": "🔴 SELL" if diff > 0 else "🟢 BUY",
                "amount": abs(diff) * total_value,
            })
    
    return {"rebalancing": rebalancing, "total_value": total_value}


def get_dividend_info(portfolio_positions, ticker_data: dict) -> dict:
    """Get dividend information for portfolio."""
    dividend_data = []
    total_dividend_yield = 0
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        info = fetch_ticker_info(ticker)
        
        dividend_yield = info.get("dividend_yield", 0)
        if dividend_yield and dividend_yield > 0:
            df = ticker_data.get(ticker, pd.DataFrame())
            if not df.empty:
                price = df["Close"].iloc[-1]
                position_value = pos["qty"] * price
                annual_dividend = position_value * dividend_yield
                
                dividend_data.append({
                    "ticker": ticker,
                    "yield": dividend_yield * 100,
                    "annual_dividend": annual_dividend,
                    "position_value": position_value,
                })
                
                total_dividend_yield += dividend_yield * position_value
    
    return {
        "dividends": dividend_data,
        "total_annual_income": total_dividend_yield,
        "count": len(dividend_data),
    }


def compare_vs_benchmark(portfolio_positions, ticker_data: dict) -> dict:
    """Compare portfolio performance vs S&P 500."""
    try:
        # Get portfolio return
        portfolio_value = sum(pos["qty"] * ticker_data.get(pos["ticker"], pd.DataFrame())["Close"].iloc[-1] 
                             for pos in portfolio_positions 
                             if not ticker_data.get(pos["ticker"], pd.DataFrame()).empty)
        portfolio_cost = sum(pos["qty"] * pos["gak"] for pos in portfolio_positions)
        portfolio_return = (portfolio_value - portfolio_cost) / portfolio_cost * 100 if portfolio_cost > 0 else 0
        
        # Get S&P 500 return (1 year)
        sp500 = yf.Ticker("^GSPC")
        sp500_hist = sp500.history(period="1y", progress=False)
        
        if not sp500_hist.empty and len(sp500_hist) > 0:
            sp500_start = sp500_hist["Close"].iloc[0]
            sp500_end = sp500_hist["Close"].iloc[-1]
            sp500_return = (sp500_end - sp500_start) / sp500_start * 100
        else:
            sp500_return = float("nan")
        
        return {
            "portfolio_return": portfolio_return,
            "sp500_return": sp500_return,
            "outperformance": portfolio_return - sp500_return if not pd.isna(sp500_return) else float("nan"),
        }
    except:
        return {}


def get_stock_recommendations(watchlist_tickers: list, portfolio_tickers: list, ticker_data: dict, popular_tickers: list = None) -> list:
    """Get enterprise-grade stock recommendations with multiple filters."""
    recommendations = []
    
    # Default popular stocks (broader selection, not just S&P 500)
    if popular_tickers is None:
        popular_tickers = [
            # Tech
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "AMD", "INTC",
            # Finance
            "JPM", "BAC", "GS", "WFC", "V", "MA", "AXP", "BLK", "BX",
            # Healthcare
            "JNJ", "UNH", "PFE", "MRK", "ABBV", "TMO", "LLY", "AZN",
            # Consumer
            "WMT", "MCD", "SBUX", "NKE", "HD", "COST", "CVX", "XOM",
            # Growth/Small Cap
            "SOFI", "PYPL", "SQ", "COIN", "CRWD", "ZS", "OKTA", "DDOG", "CRM", "ADBE",
            # International/ETFs
            "VTI", "VOO", "QQQ", "IVV", "SPLG", "SCHX",
            # More diverse
            "UBER", "LYFT", "DASH", "ZM", "ROKU", "SNAP", "PINTEREST", "TWILIO",
            "RBLX", "U", "PLAN", "UPST", "ABNB", "LCID", "RIVN", "PLTR"
        ]
    
    # Combine watchlist + popular stocks, remove duplicates
    # NOTE: We NO LONGER exclude portfolio holdings - users want to see all recommendations
    all_candidates = list(set(watchlist_tickers + popular_tickers + portfolio_tickers))
    
    # Sector mapping for diversification
    sector_map = {
        "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "Tech", "NVDA": "Tech", "META": "Tech",
        "TSLA": "Auto", "NFLX": "Media", "AMD": "Tech", "INTC": "Tech", "ADBE": "Tech", "CRM": "Tech",
        "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "WFC": "Finance", "V": "Finance", "MA": "Finance",
        "AXP": "Finance", "BLK": "Finance", "BX": "Finance",
        "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare", "ABBV": "Healthcare",
        "TMO": "Healthcare", "LLY": "Healthcare", "AZN": "Healthcare",
        "WMT": "Consumer", "MCD": "Consumer", "SBUX": "Consumer", "NKE": "Consumer", "HD": "Consumer", "COST": "Consumer",
        "CVX": "Energy", "XOM": "Energy",
        "SOFI": "Finance", "PYPL": "Finance", "SQ": "Finance", "COIN": "Crypto", "CRWD": "Tech", "ZS": "Tech",
        "OKTA": "Tech", "DDOG": "Tech",
        "UBER": "Transport", "LYFT": "Transport", "DASH": "Transport", "ZM": "Tech", "ROKU": "Media",
        "SNAP": "Media", "TWILIO": "Tech", "RBLX": "Gaming", "U": "Auto", "PLTR": "Tech"
    }
    
    for ticker in all_candidates:
        df = ticker_data.get(ticker, pd.DataFrame())
        if df.empty or len(df) < 50:  # Need minimum 50 days of data
            continue
        
        price = df["Close"].iloc[-1] if len(df) > 0 else 0
        if price == 0 or price < 0.5:  # Skip penny stocks
            continue
        
        # Calculate metrics
        try:
            rsi = ta_lib.momentum.rsi(df["Close"], window=14).iloc[-1] if len(df) >= 14 else float("nan")
            sma200 = ta_lib.trend.sma_indicator(df["Close"], window=200).iloc[-1] if len(df) >= 200 else float("nan")
            sma50 = ta_lib.trend.sma_indicator(df["Close"], window=50).iloc[-1] if len(df) >= 50 else float("nan")
            fair_val = calculate_fair_value(df, price, ticker)
            ticker_info = fetch_ticker_info(ticker)
        except:
            continue
        
        upside = fair_val["upside"]
        reliability = fair_val["reliability"]
        
        # FILTER 1: Only exclude EXTREME (allow Good, Moderate, High Uncertainty)
        if "EXTREME" in reliability:
            continue
        
        # FILTER 2: Only buy if significant upside (at least 5% - lowered threshold)
        if upside < 5:
            continue
        
        # FILTER 3: Avoid extreme overbought (RSI > 80 - raised threshold)
        if not pd.isna(rsi) and rsi > 80:
            continue
        
        # FILTER 4: P/E ratio check (if available) - avoid very overpriced
        pe_ratio = ticker_info.get("pe_ratio")
        if pe_ratio and pe_ratio > 70:  # Raised from 50 to 70
            continue
        
        # FILTER 5: FUNDAMENTAL HEALTH CHECKS (optional, don't block if data missing)
        # Check earnings growth (positive or at least not super negative)
        earnings_growth = ticker_info.get("earnings_growth")
        if earnings_growth is not None and earnings_growth < -0.75:  # Only skip if earnings down >75%
            continue
        
        # Check debt/equity (avoid extremely over-leveraged)
        debt_to_equity = ticker_info.get("debt_to_equity")
        if debt_to_equity and debt_to_equity > 4.0:  # Raised from 3.0 to 4.0
            continue
        
        # Check profit margin (only skip if deeply unprofitable)
        profit_margin = ticker_info.get("profit_margin")
        if profit_margin is not None and profit_margin < -0.40:  # Raised from -0.25 to -0.40
            continue
        
        # Check ROE (return on equity - quality indicator, but don't be too strict)
        roe = ticker_info.get("roe")
        if roe is not None and roe < -0.05:  # Only skip if negative ROE (very poor)
            continue
        
        # Scoring system - Enterprise version
        score = 0
        reasons = []
        
        # 1. FAIR VALUE is the main signal
        if upside > 30:
            score += 6
            reasons.append("Sterk ondergewaardeerd (>30%)")
        elif upside > 20:
            score += 5
            reasons.append("Zeer ondergewaardeerd (20-30%)")
        elif upside > 15:
            score += 4
            reasons.append("Ondergewaardeerd (15-20%)")
        elif upside > 8:
            score += 2
            reasons.append("Matig ondergewaardeerd (8-15%)")
        
        # 2. RSI Entry Point (critical for timing)
        if not pd.isna(rsi):
            if rsi < 25:
                score += 3
                reasons.append("Extreme oversold - beste entry")
            elif rsi < 30:
                score += 2
                reasons.append("Oversold - goede entry")
            elif rsi < 40:
                score += 1
                reasons.append("Oversold signaal")
        
        # 3. Trend Confirmation
        if not pd.isna(sma200) and not pd.isna(sma50):
            if price > sma200 and sma50 > sma200:
                score += 2
                reasons.append("Strong uptrend (SMA50 > SMA200)")
            elif price > sma200:
                score += 1
                reasons.append("Uptrend confirmed")
        
        # 4. Volume Confirmation
        if len(df) >= 20:
            recent_vol = df["Volume"].tail(5).mean()
            historical_vol = df["Volume"].tail(60).mean()
            if recent_vol > historical_vol * 1.5:
                score += 2
                reasons.append("Volume surge - momentum confirmed")
            elif recent_vol > historical_vol * 1.2:
                score += 1
                reasons.append("Above average volume")
        
        # 5. Momentum (Price > SMA50 > SMA200)
        if not pd.isna(sma50) and not pd.isna(sma200):
            if price > sma50 > sma200:
                score += 1
                reasons.append("Golden cross setup")
        
        # 6. Valuation health (P/E not too high)
        if pe_ratio and 15 < pe_ratio < 30:
            score += 1
            reasons.append("Reasonable P/E ratio")
        
        # 7. FUNDAMENTAL QUALITY BONUSES (optional - if data is available)
        # Earnings growth bonus
        earnings_growth = ticker_info.get("earnings_growth")
        if earnings_growth and earnings_growth > 0.20:  # >20% earnings growth
            score += 2
            reasons.append("Strong earnings growth (>20%)")
        elif earnings_growth and earnings_growth > 0.10:  # >10% earnings growth
            score += 1
            reasons.append("Solid earnings growth (>10%)")
        
        # ROE quality check (higher ROE = better capital efficiency)
        roe = ticker_info.get("roe")
        if roe and roe > 0.20:  # ROE >20% = excellent
            score += 2
            reasons.append("Excellent ROE (>20%)")
        elif roe and roe > 0.12:  # ROE >12% = good
            score += 1
            reasons.append("Good ROE (>12%)")
        
        # Debt health (lower is better)
        debt_to_equity = ticker_info.get("debt_to_equity")
        if debt_to_equity and debt_to_equity < 0.3:  # Very low debt = safer
            score += 1
            reasons.append("Very strong balance sheet")
        elif debt_to_equity and debt_to_equity < 0.7:  # Moderate debt
            score += 0.5
            reasons.append("Solid balance sheet")
        
        # Profit margin (higher = healthier business)
        profit_margin = ticker_info.get("profit_margin")
        if profit_margin and profit_margin > 0.25:  # >25% profit margin
            score += 1
            reasons.append("Excellent profit margins (>25%)")
        elif profit_margin and profit_margin > 0.15:  # >15% profit margin
            score += 0.5
            reasons.append("Good profit margins")
        
        # Only recommend if score >= 3 (lowered from 4)
        if score >= 3 and reasons:
            # Calculate stop loss (support level)
            support = calculate_support_resistance(df).get("support1", price * 0.90)
            stop_loss_pct = ((price - support) / price * 100) if price > 0 else 5
            
            # Check if already owned
            already_owned = ticker in portfolio_tickers
            
            recommendations.append({
                "ticker": ticker,
                "price": price,
                "fair_value": fair_val["fair_value"],
                "upside": upside,
                "rsi": rsi,
                "score": score,
                "reliability": reliability,
                "reasons": " | ".join(reasons),
                "rating": "🟢 STRONG BUY" if score >= 13 else ("🟢 BUY" if score >= 9 else "🟡 BUY"),
                "sector": sector_map.get(ticker, "Other"),
                "pe_ratio": pe_ratio,
                "stop_loss": support,
                "stop_loss_pct": stop_loss_pct,
                "owned": already_owned,  # NEW: Track if user already owns this stock
            })
    
    # FILTER 5: Sector Diversification - max 2 per sector in top results
    sector_counts = {}
    filtered_recommendations = []
    
    for rec in sorted(recommendations, key=lambda x: (x["score"], x["upside"]), reverse=True):
        sector = rec["sector"]
        if sector_counts.get(sector, 0) < 3:  # Allow 3 per sector now
            filtered_recommendations.append(rec)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(filtered_recommendations) >= 20:  # Show up to 20 recommendations
            break
    
    return filtered_recommendations


def calculate_portfolio_metrics(portfolio_positions, ticker_data: dict) -> dict:
    """Calculate portfolio metrics."""
    if not portfolio_positions:
        return {}
    
    total_invested = 0
    total_current = 0
    sectors = {}
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        qty = pos["qty"]
        gak = pos["gak"]
        
        cost = qty * gak
        total_invested += cost
        
        df = ticker_data.get(ticker, pd.DataFrame())
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            current_value = qty * current_price
            total_current += current_value
            
            # Get sector
            info = fetch_ticker_info(ticker)
            sector = info.get("sector", "Unknown")
            if sector not in sectors:
                sectors[sector] = 0
            sectors[sector] += current_value
    
    return {
        "total_invested": total_invested,
        "total_current": total_current,
        "total_return": total_current - total_invested,
        "total_return_pct": ((total_current - total_invested) / total_invested * 100) if total_invested > 0 else 0,
        "sectors": sectors,
    }


def build_candlestick(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Candlestick chart with SMA200 overlay + RSI sub-chart."""
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.70, 0.30],
        subplot_titles=(f"{ticker} – Candlestick + SMA200", "RSI (14)"),
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"],
            name="Price",
            increasing_line_color="#3fb950",
            decreasing_line_color="#f85149",
        ),
        row=1, col=1,
    )

    # SMA200
    if "SMA200" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["SMA200"],
                name="SMA 200",
                line=dict(color="#58a6ff", width=1.8, dash="dot"),
            ),
            row=1, col=1,
        )

    # RSI
    if "RSI" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["RSI"],
                name="RSI",
                line=dict(color="#e3b341", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(227,179,65,0.08)",
            ),
            row=2, col=1,
        )
        # Overbought / Oversold reference lines
        for lvl, col in [(70, "#f85149"), (30, "#3fb950")]:
            fig.add_hline(
                y=lvl, line_dash="dash",
                line_color=col, opacity=0.5,
                row=2, col=1,
            )

    _tpl = "plotly_dark" if st.session_state.get('theme', 'dark') == 'dark' else "plotly_white"
    _bg = "#0e1117" if st.session_state.get('theme', 'dark') == 'dark' else "#ffffff"
    fig.update_layout(
        template=_tpl,
        paper_bgcolor=_bg,
        plot_bgcolor=_bg,
        height=620,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    fig.update_xaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/stocks.png",
        width=64,
    )
    st.title("📊 Stock Dashboard")
    st.caption(f"Last refresh: {datetime.now().strftime('%d %b %Y  %H:%M')}")
    
    # Theme toggle
    theme_cols = st.columns([1, 1])
    with theme_cols[0]:
        if st.button("🌙 Nacht" if st.session_state.theme == "light" else "☀️ Dag", use_container_width=True):
            st.session_state.theme = "dark" if st.session_state.theme == "light" else "light"
            st.rerun()
    with theme_cols[1]:
        current_theme = "🌙 Dark" if st.session_state.theme == "dark" else "☀️ Light"
        st.caption(f"Mode: {current_theme}")
    
    st.divider()

    # --- Login / User Selection ---
    st.subheader("👤 Gebruiker")
    
    existing_users = get_all_users()
    if "current_user" not in st.session_state:
        st.session_state.current_user = None
    
    # Option to login or create new user
    user_action = st.radio(
        "Wat wil je doen?",
        ["Inloggen", "Nieuwe gebruiker"],
        label_visibility="collapsed",
        horizontal=True,
    )
    
    if user_action == "Inloggen" and existing_users:
        selected_user = st.selectbox(
            "Selecteer gebruiker",
            options=existing_users,
            label_visibility="collapsed",
        )
        if st.button("🔓 Inloggen", use_container_width=True):
            st.session_state.current_user = selected_user
            st.rerun()
    elif user_action == "Nieuwe gebruiker":
        new_user = st.text_input(
            "Gebruikersnaam",
            placeholder="bijv. papa, broer, jij",
            label_visibility="collapsed",
        ).strip().lower()
        if new_user:
            if new_user in existing_users:
                st.warning("Deze gebruiker bestaat al!")
            elif st.button("✅ Registreer", use_container_width=True):
                # Create new user with default data
                save_user_portfolio(new_user, {"portfolio_raw": "", "watchlist_raw": ""})
                st.session_state.current_user = new_user
                st.success(f"Welkom, {new_user}! 🎉")
                st.rerun()
    
    if st.session_state.current_user:
        st.divider()
        st.info(f"✅ Ingelogd als: **{st.session_state.current_user}**")
        if st.button("🚪 Uitloggen", use_container_width=True):
            st.session_state.current_user = None
            st.rerun()
    else:
        st.warning("⚠️ Gelieve in te loggen of een nieuwe gebruiker aan te maken.")
        st.stop()

    # Load current user's data
    user_data = load_user_portfolio(st.session_state.current_user)
    portfolio_raw = user_data.get("portfolio_raw", "")
    watchlist_raw = user_data.get("watchlist_raw", "")

    # --- Portfolio input - Easy form ---
    st.subheader("🗂️ Portfolio")
    
    col1, col2, col3, col4 = st.columns([1.5, 0.8, 0.8, 0.9])
    with col1:
        new_ticker = st.text_input("Ticker", placeholder="AAPL", label_visibility="collapsed").upper()
    with col2:
        new_qty = st.number_input("Aantal", value=1.0, min_value=0.1, step=0.1, label_visibility="collapsed")
    with col3:
        new_price = st.number_input("Prijs", value=100.0, min_value=0.01, step=0.01, label_visibility="collapsed")
    with col4:
        new_currency = st.selectbox("Munt", options=["USD", "EUR"], label_visibility="collapsed")
    
    if st.button("➕ Stock toevoegen", use_container_width=True):
        if new_ticker:
            # Parse existing portfolio
            existing_lines = [l.strip() for l in portfolio_raw.strip().split('\n') if l.strip()]
            # Add new stock with currency indicator
            existing_lines.append(f"{new_ticker}, {new_qty}, {new_price}, {new_currency}")
            portfolio_raw = '\n'.join(existing_lines)
            # Auto-save
            save_user_portfolio(st.session_state.current_user, {
                "portfolio_raw": portfolio_raw,
                "watchlist_raw": watchlist_raw,
            })
            st.success(f"✅ {new_ticker} ({new_currency}) toegevoegd!")
            st.rerun()
        else:
            st.error("⚠️ Voer een ticker in")
    
    st.caption("Huidige posities (formaat: TICKER, AANTAL, PRIJS, MUNT):")
    if portfolio_raw:
        st.text_area(
            "Posities",
            value=portfolio_raw,
            height=120,
            disabled=True,
            label_visibility="collapsed",
        )
    else:
        st.info("Nog geen stocks in je portfolio")

    # --- Remove stock ---
    if portfolio_raw:
        st.caption("Stock verwijderen:")
        existing_stocks = [l.strip().split(',')[0] for l in portfolio_raw.strip().split('\n') if l.strip()]
        remove_ticker = st.selectbox("Kies stock om te verwijderen", options=existing_stocks, label_visibility="collapsed")
        if st.button("🗑️ Verwijderen", use_container_width=True):
            remaining_lines = [l for l in portfolio_raw.strip().split('\n') if l.strip() and not l.strip().startswith(remove_ticker)]
            portfolio_raw = '\n'.join(remaining_lines)
            save_user_portfolio(st.session_state.current_user, {
                "portfolio_raw": portfolio_raw,
                "watchlist_raw": watchlist_raw,
                "price_alerts": user_data.get("price_alerts", []),
                "portfolio_history": user_data.get("portfolio_history", []),
                "watchlist_categories": user_data.get("watchlist_categories", {}),
            })
            st.success(f"✅ {remove_ticker} verwijderd!")
            st.rerun()

    # --- Bulk Import ---
    st.divider()
    st.subheader("📥 Bulk Import")
    st.caption("Plak meerdere stocks (één per regel: TICKER, AANTAL, PRIJS, MUNT)")
    
    bulk_input = st.text_area(
        "Bulk import",
        height=150,
        placeholder="AMD, 62.0, 151.06\nMETA, 7.0, 768.47\nGOOG, 14.0, 186.35, USD",
        label_visibility="collapsed",
    )
    
    if st.button("📥 Importeer Alle", use_container_width=True):
        if bulk_input.strip():
            # Parse existing portfolio
            existing_lines = [l.strip() for l in portfolio_raw.strip().split('\n') if l.strip()]
            
            # Add new lines from bulk input
            new_lines = [l.strip() for l in bulk_input.strip().split('\n') if l.strip()]
            
            # Combine
            all_lines = existing_lines + new_lines
            portfolio_raw = '\n'.join(all_lines)
            
            # Save
            save_user_portfolio(st.session_state.current_user, {
                "portfolio_raw": portfolio_raw,
                "watchlist_raw": watchlist_raw,
                "price_alerts": user_data.get("price_alerts", []),
                "portfolio_history": user_data.get("portfolio_history", []),
                "watchlist_categories": user_data.get("watchlist_categories", {}),
            })
            st.success(f"✅ {len(new_lines)} stocks geïmporteerd!")
            st.rerun()
        else:
            st.error("⚠️ Plak eerst stocks om te importeren")

    # --- Watchlist input ---
    st.divider()
    st.subheader("👁️ Watchlist")
    watchlist_raw = st.text_input(
        "Tickers (komma-gescheiden)",
        value=watchlist_raw,
        label_visibility="collapsed",
    )
    
    if st.button("💾 Watchlist opslaan", use_container_width=True):
        save_user_portfolio(st.session_state.current_user, {
            "portfolio_raw": portfolio_raw,
            "watchlist_raw": watchlist_raw,
        })
        st.success("✅ Watchlist opgeslagen!")
    
    st.divider()
    if st.button("🔄 Ververs data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # --- Email Configuration ---
    st.divider()
    st.subheader("📧 Email Alerts")
    
    email_config = user_data.get("email_config", {})
    
    with st.expander("⚙️ Email Configuratie", expanded=False):
        st.caption("Configureer SMTP instellingen voor email alerts")
        
        # Email provider selection
        current_smtp = email_config.get("smtp_server", "")
        default_idx = 1 if "office365" in current_smtp or "outlook" in current_smtp.lower() else 0
        email_provider = st.selectbox(
            "Email Provider",
            options=["Gmail", "Outlook/Hotmail", "Yahoo", "Custom SMTP"],
            index=default_idx,
            key="email_provider"
        )
        
        # Pre-fill SMTP settings based on provider
        if email_provider == "Gmail":
            default_smtp = "smtp.gmail.com"
            default_port = 587
            st.info("💡 Voor Gmail: maak een [App Password](https://myaccount.google.com/apppasswords) aan.")
        elif email_provider == "Outlook/Hotmail":
            default_smtp = "smtp.office365.com"
            default_port = 587
        elif email_provider == "Yahoo":
            default_smtp = "smtp.mail.yahoo.com"
            default_port = 587
        else:
            default_smtp = email_config.get("smtp_server", "smtp.gmail.com")
            default_port = email_config.get("smtp_port", 587)
        
        cfg_smtp = st.text_input("SMTP Server", value=email_config.get("smtp_server", default_smtp), key="cfg_smtp")
        cfg_port = st.number_input("SMTP Port", value=email_config.get("smtp_port", default_port), min_value=1, max_value=65535, key="cfg_port")
        cfg_sender = st.text_input("Jouw Email", value=email_config.get("sender_email", ""), placeholder="jouw@email.com", key="cfg_sender")
        cfg_password = st.text_input("App Password", value=email_config.get("sender_password", ""), type="password", key="cfg_password")
        cfg_recipient = st.text_input("Ontvanger Email", value=email_config.get("recipient_email", ""), placeholder="ontvanger@email.com", key="cfg_recipient")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Opslaan", use_container_width=True, key="save_email_cfg"):
                email_config = {
                    "smtp_server": cfg_smtp,
                    "smtp_port": int(cfg_port),
                    "sender_email": cfg_sender,
                    "sender_password": cfg_password,
                    "recipient_email": cfg_recipient,
                }
                user_data["email_config"] = email_config
                save_user_portfolio(st.session_state.current_user, user_data)
                st.success("✅ Email configuratie opgeslagen!")
        
        with col2:
            if st.button("📧 Test Email", use_container_width=True, key="test_email"):
                if cfg_sender and cfg_password and cfg_recipient:
                    test_config = {
                        "smtp_server": cfg_smtp,
                        "smtp_port": int(cfg_port),
                        "sender_email": cfg_sender,
                        "sender_password": cfg_password,
                    }
                    success, message = send_email_alert(
                        cfg_recipient,
                        "🧪 Test Email - Stock Dashboard",
                        "<h2>✅ Test Succesvol!</h2><p>Je email configuratie werkt correct. Je ontvangt nu alerts op dit adres.</p>",
                        test_config
                    )
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                else:
                    st.error("⚠️ Vul alle email velden in")


# ── Parse inputs ──────────────────────────────────────────────────────────────
portfolio_positions = []
eur_to_usd = 1.10  # Exchange rate EUR to USD

for line in portfolio_raw.strip().splitlines():
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 3:
        try:
            ticker = parts[0].upper()
            qty = float(parts[1])
            price = float(parts[2])
            currency = parts[3].upper() if len(parts) > 3 else "USD"
            
            # Convert EUR to USD if needed
            if currency == "EUR":
                price = price * eur_to_usd
            
            portfolio_positions.append({
                "ticker": ticker,
                "qty": qty,
                "gak": price,  # Always in USD
            })
        except ValueError:
            pass

watchlist_tickers = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]

# Popular stocks for recommendations
popular_tickers_list = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "GS", "WFC", "V", "MA", "AXP", "BLK", "BX",
    "JNJ", "UNH", "PFE", "MRK", "ABBV", "TMO", "LLY", "AZN",
    "WMT", "MCD", "SBUX", "NKE", "HD", "COST", "CVX", "XOM",
    "SOFI", "PYPL", "COIN", "CRWD", "ZS", "OKTA", "DDOG", "CRM", "ADBE",
    "VTI", "VOO", "QQQ", "IVV", "SPLG", "SCHX",
    "UBER", "LYFT", "DASH", "ZM", "ROKU", "SNAP", "RBLX", "PLTR", "ARM", "SMCI"
]

all_tickers = list({p["ticker"] for p in portfolio_positions} | set(watchlist_tickers))


# ── Fetch all data once ───────────────────────────────────────────────────────
with st.spinner("📡 Data ophalen…"):
    ticker_data: dict[str, pd.DataFrame] = {t: fetch_data(t) for t in all_tickers}


# ── Helper: latest indicators ─────────────────────────────────────────────────
def latest(ticker: str, col: str) -> float:
    df = ticker_data.get(ticker, pd.DataFrame())
    if df.empty or col not in df.columns:
        return float("nan")
    series = df[col]
    # Flatten in case it's still a DataFrame/multi-column
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    series = series.dropna()
    if series.empty:
        return float("nan")
    return float(series.iloc[-1])


# ── Market Dashboard Header ───────────────────────────────────────────────────
@st.cache_data(ttl=120)  # Cache 2 minutes
def get_market_indices() -> dict:
    """Fetch major market indices for dashboard header."""
    indices = {}
    index_map = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "VIX": "^VIX",
        "EUR/USD": "EURUSD=X",
        "BTC": "BTC-USD",
    }
    for name, symbol in index_map.items():
        try:
            data = yf.download(symbol, period="2d", progress=False, auto_adjust=True)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            if not data.empty and len(data) >= 2:
                current = float(data["Close"].iloc[-1])
                prev = float(data["Close"].iloc[-2])
                change = current - prev
                change_pct = (change / prev * 100) if prev > 0 else 0
                indices[name] = {"price": current, "change": change, "change_pct": change_pct}
            elif not data.empty:
                current = float(data["Close"].iloc[-1])
                indices[name] = {"price": current, "change": 0, "change_pct": 0}
        except Exception:
            pass
    return indices

market_indices = get_market_indices()

if market_indices:
    idx_html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; justify-content:center">'
    for name, data in market_indices.items():
        color = "#3fb950" if data["change_pct"] >= 0 else "#f85149"
        arrow = "▲" if data["change_pct"] >= 0 else "▼"
        
        if name == "BTC":
            price_str = f"${data['price']:,.0f}"
        elif name == "EUR/USD":
            price_str = f"{data['price']:.4f}"
        elif name == "VIX":
            price_str = f"{data['price']:.2f}"
        else:
            price_str = f"{data['price']:,.0f}"
        
        idx_html += f'''
        <div style="flex:1 1 120px; min-width:100px; max-width:180px; text-align:center; padding:8px 12px; background:{'#141a24' if st.session_state.theme == 'dark' else '#f0f4f8'}; border-radius:10px; border:1px solid {'#1e3a5f' if st.session_state.theme == 'dark' else '#c0d4e8'}">
            <div style="font-size:0.7rem; color:{'#7a8599' if st.session_state.theme == 'dark' else '#666'}; font-weight:600">{name}</div>
            <div style="font-size:0.95rem; font-weight:800; color:{'#e6edf3' if st.session_state.theme == 'dark' else '#000'}">{price_str}</div>
            <div style="font-size:0.75rem; font-weight:700; color:{color}">{arrow} {data['change_pct']:+.2f}%</div>
        </div>
        '''
    idx_html += '</div>'
    st.markdown(idx_html, unsafe_allow_html=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13, tab14, tab15 = st.tabs([
    "📁 Portfolio", 
    "👁️ Watchlist", 
    "📊 Analytics", 
    "⚙️ Advanced", 
    "🎯 Tips",
    "⚠️ Alerts",
    "💡 Rebalance",
    "💰 Income",
    "🔔 Prices",
    "📈 History",
    "🧮 Calculator",
    "🎬 YouTube",
    "📅 Earnings",
    "📰 News",
    "🔍 Screener"
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 – Portfolio
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Portfolio Overzicht")

    if not portfolio_positions:
        st.info("Voeg posities toe in de zijbalk (TICKER, AANTAL, GAK).")
    else:
        rows = []
        for pos in portfolio_positions:
            t = pos["ticker"]
            price = latest(t, "Close")
            rsi   = latest(t, "RSI")
            sma   = latest(t, "SMA200")
            sma50 = latest(t, "SMA50")
            qty   = pos["qty"]
            gak   = pos["gak"]
            wl    = price * qty if price else 0.0
            cost  = gak * qty
            pnl   = wl - cost
            pnl_p = (pnl / cost * 100) if cost else 0.0
            df = ticker_data.get(t, pd.DataFrame())
            fair_val = calculate_fair_value(df, price, t)
            status = classify_status(price, sma, rsi, df, fair_val["fair_value"])
            
            # Daily change
            daily_change = latest(t, "Change_Pct")
            
            # Get additional info
            info = fetch_ticker_info(t)
            sector = info.get("sector", "N/A")
            div_yield = info.get("dividend_yield", 0) or 0
            
            rows.append({
                "Ticker": t,
                "Prijs": price,
                "Fair Value": fair_val["fair_value"],
                "GAK": gak,
                "Aantal": qty,
                "Waarde ($)": wl,
                "Kostprijs": cost,
                "W/V ($)": pnl,
                "W/V (%)": pnl_p,
                "RSI": rsi,
                "SMA50": sma50,
                "SMA200": sma,
                "Daily_Change": daily_change,
                "Div_Yield": div_yield,
                "_status": status,
                "_fair_val_str": fair_val["valuation"],
                "_reliability": fair_val.get("reliability", "✅ Good"),
                "Sector": sector,
            })

        total_value  = sum(r["Waarde ($)"] for r in rows)
        total_cost   = sum(r["Kostprijs"] for r in rows)
        total_pnl    = total_value - total_cost
        total_pnl_p  = (total_pnl / total_cost * 100) if total_cost else 0.0
        
        # Sort by performance (best first)
        rows_sorted = sorted(rows, key=lambda x: x["W/V (%)"], reverse=True)

        # ── Summary Cards ──
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric(
            "💼 Geïnvesteerd",
            f"$ {total_cost:,.2f}",
        )
        
        pnl_delta = f"{total_pnl:+,.0f}" if total_pnl != 0 else "0"
        col2.metric(
            "💰 Huidige Waarde",
            f"$ {total_value:,.2f}",
            delta=pnl_delta,
        )
        
        pnl_color = "normal" if total_pnl >= 0 else "inverse"
        col3.metric(
            "📈 Totaal W/V",
            f"$ {total_pnl:+,.2f}",
            delta=f"{total_pnl_p:+.2f}%",
            delta_color=pnl_color,
        )
        
        # Day change
        day_change_value = sum(
            float(r["Waarde ($)"]) * float(r["Daily_Change"]) / 100
            for r in rows if not pd.isna(r["Daily_Change"])
        )
        day_change_pct = (day_change_value / total_value * 100) if total_value > 0 else 0
        col4.metric(
            "📅 Vandaag",
            f"$ {day_change_value:+,.2f}",
            delta=f"{day_change_pct:+.2f}%",
            delta_color="normal" if day_change_value >= 0 else "inverse",
        )
        
        # Best performer today
        best_today = max(rows, key=lambda x: x["Daily_Change"] if not pd.isna(x["Daily_Change"]) else -999)
        worst_today = min(rows, key=lambda x: x["Daily_Change"] if not pd.isna(x["Daily_Change"]) else 999)
        col5.metric(
            f"🏆 Best: {best_today['Ticker']}",
            f"{best_today['Daily_Change']:+.2f}%" if not pd.isna(best_today['Daily_Change']) else "N/A",
            delta=f"Worst: {worst_today['Ticker']} {worst_today['Daily_Change']:+.1f}%" if not pd.isna(worst_today['Daily_Change']) else None,
        )
        
        st.divider()
        
        # ── Sector Breakdown ──
        st.subheader("🏢 Sector Performance")
        sector_stats = {}
        for row in rows_sorted:
            sector = row["Sector"]
            if sector not in sector_stats:
                sector_stats[sector] = {"value": 0, "cost": 0, "pnl": 0}
            sector_stats[sector]["value"] += row["Waarde ($)"]
            sector_stats[sector]["cost"] += row["Kostprijs"]
            sector_stats[sector]["pnl"] += row["W/V ($)"]
        
        sector_cols = st.columns(len(sector_stats))
        for col, (sector, stats) in zip(sector_cols, sector_stats.items()):
            pnl_pct = (stats["pnl"] / stats["cost"] * 100) if stats["cost"] > 0 else 0
            col.metric(
                f"📌 {sector}",
                f"$ {stats['value']:,.0f}",
                delta=f"{pnl_pct:+.1f}%"
            )
        
        st.divider()
        
        # ── Detailed Holdings Table ──
        st.subheader("📋 Holdings")
        
        for idx, r in enumerate(rows_sorted, 1):
            # Color coding
            pnl = float(r["W/V ($)"])
            pnl_p = float(r["W/V (%)"])
            pnl_color = "#3fb950" if pnl >= 0 else "#f85149"
            daily = float(r["Daily_Change"]) if not pd.isna(r["Daily_Change"]) else 0
            daily_color = "#3fb950" if daily >= 0 else "#f85149"
            
            rsi = float(r["RSI"]) if not pd.isna(r["RSI"]) else 50
            rsi_color = "#3fb950" if rsi < 35 else ("#f85149" if rsi > 65 else "#e3b341")
            
            # Determine card border
            if r["_status"] == "BUY":
                border = "#3fb950"
            elif r["_status"] == "SELL":
                border = "#f85149"
            else:
                border = "#2e3140"
            
            bg = "#141a24" if st.session_state.theme == "dark" else "#f8fafc"
            text_color = "#e6edf3" if st.session_state.theme == "dark" else "#000"
            sub_color = "#8b949e" if st.session_state.theme == "dark" else "#666"
            card_bg = "#0f1319" if st.session_state.theme == "dark" else "#f0f4f8"
            
            st.markdown(f"""
            <div style="background:{bg}; border:2px solid {border}; border-radius:14px; padding:16px; margin-bottom:10px;">
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px">
                    <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap">
                        <span style="font-size:1.4rem; font-weight:800; color:{text_color}">{r['Ticker']}</span>
                        <span style="font-size:0.75rem; color:{sub_color}">{r['Sector'][:20]}</span>
                        {status_badge(r["_status"])}
                    </div>
                    <div style="text-align:right">
                        <div style="font-size:1.2rem; font-weight:700; color:{text_color}">${float(r['Prijs']):.2f}</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{daily_color}">{daily:+.2f}% today</div>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(90px, 1fr)); gap:8px;">
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">Waarde</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{text_color}">${float(r['Waarde ($)']):,.2f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">W/V</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{pnl_color}">${pnl:+,.2f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">Return</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{pnl_color}">{pnl_p:+.1f}%</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">Fair Val</div>
                        <div style="font-size:0.9rem; font-weight:700; color:#58a6ff">${float(r['Fair Value']):.2f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">RSI</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{rsi_color}">{rsi:.0f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">Aantal</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{text_color}">{float(r['Aantal']):.2f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">GAK</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{text_color}">${float(r['GAK']):.2f}</div>
                    </div>
                    <div style="text-align:center; padding:8px; background:{card_bg}; border-radius:8px">
                        <div style="font-size:0.65rem; color:{sub_color}">Div Yield</div>
                        <div style="font-size:0.9rem; font-weight:700; color:{'#3fb950' if r['Div_Yield'] > 0.02 else sub_color}">{r['Div_Yield']*100:.1f}%</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        st.divider()
        
        # ── Portfolio Performance Chart ──
        st.subheader("📈 Performance Overview (30D)")
        
        # Build a combined performance chart for all portfolio stocks
        perf_fig = go.Figure()
        for row in rows_sorted[:10]:  # Top 10
            t = row["Ticker"]
            df = ticker_data.get(t, pd.DataFrame())
            if not df.empty and len(df) >= 30:
                close = df["Close"].tail(30)
                # Normalize to percentage from start
                normalized = (close / close.iloc[0] - 1) * 100
                color = "#3fb950" if float(normalized.iloc[-1]) >= 0 else "#f85149"
                perf_fig.add_trace(go.Scatter(
                    x=df.index[-30:], y=normalized, 
                    name=f"{t} ({float(normalized.iloc[-1]):+.1f}%)",
                    line=dict(width=2),
                    hovertemplate=f"{t}: %{{y:+.2f}}%<extra></extra>"
                ))
        
        perf_fig.add_hline(y=0, line_dash="dash", line_color="#8b949e", opacity=0.5)
        perf_fig.update_layout(
            template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
            height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
            yaxis_title="Return %",
            xaxis_title="",
        )
        st.plotly_chart(perf_fig, use_container_width=True)
        
        st.divider()
        
        # ── Portfolio Composition ──
        st.subheader("💡 Portfolio Composition")
        
        comp_cols = st.columns(2)
        
        # Asset allocation by sector (pie)
        with comp_cols[0]:
            sector_values = {}
            for row in rows:
                sector = row["Sector"]
                sector_values[sector] = sector_values.get(sector, 0) + float(row["Waarde ($)"])
            
            if sector_values:
                fig_pie = go.Figure(data=[go.Pie(
                    labels=list(sector_values.keys()),
                    values=list(sector_values.values()),
                    hole=0.3,
                    marker=dict(colors=["#3fb950", "#f85149", "#58a6ff", "#e3b341", "#bc8ef7", "#79c0ff"])
                )])
                fig_pie.update_layout(template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white", height=350, title="Sector Allocation")
                st.plotly_chart(fig_pie, use_container_width=True)
        
        # Position sizes (bar)
        with comp_cols[1]:
            position_values = {r["Ticker"]: float(r["Waarde ($)"]) for r in rows_sorted}
            position_pnl = {r["Ticker"]: float(r["W/V (%)"]) for r in rows_sorted}
            
            fig_bar = go.Figure()
            colors = ["#3fb950" if position_pnl[t] >= 0 else "#f85149" for t in position_values.keys()]
            fig_bar.add_trace(go.Bar(
                x=list(position_values.keys()),
                y=list(position_values.values()),
                marker=dict(color=colors),
                text=[f"{position_pnl[t]:.1f}%" for t in position_values.keys()],
                textposition="outside",
            ))
            fig_bar.update_layout(
                template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                height=350,
                title="Position Sizes & Returns",
                showlegend=False,
                xaxis_title="",
                yaxis_title="Value ($)"
            )
            st.plotly_chart(fig_bar, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 – Watchlist (Enhanced)
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("👁️ Watchlist Analyse Pro")
    st.info("""
    👁️ **Uitgebreide Watchlist Analyse**  
    Complete analyse van stocks die je volgt: Fair Value, technische indicatoren, 52-week range,
    volume analyse, performance metrics, entry/exit targets en buy priority ranking.
    """)

    if not watchlist_tickers:
        st.info("Voeg tickers toe in de zijbalk (komma-gescheiden).")
    else:
        # Build comprehensive watchlist data
        wl_rows = []
        for t in watchlist_tickers:
            df = ticker_data.get(t, pd.DataFrame())
            if df.empty:
                continue
                
            price = latest(t, "Close")
            rsi = latest(t, "RSI")
            sma200 = latest(t, "SMA200")
            sma50 = latest(t, "SMA50") if "SMA50" in df.columns else float("nan")
            macd = latest(t, "MACD_12_26_9") if "MACD_12_26_9" in df.columns else float("nan")
            macd_signal = latest(t, "MACDs_12_26_9") if "MACDs_12_26_9" in df.columns else float("nan")
            
            # Fair value
            fair_val = calculate_fair_value(df, price, t)
            fv = fair_val["fair_value"]
            discount_pct = ((fv - price) / price * 100) if price and fv else 0
            
            # 52-week high/low
            high_52w = df["High"].tail(252).max() if len(df) >= 252 else df["High"].max()
            low_52w = df["Low"].tail(252).min() if len(df) >= 252 else df["Low"].min()
            pct_from_high = ((price - high_52w) / high_52w * 100) if high_52w else 0
            pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w else 0
            range_position = ((price - low_52w) / (high_52w - low_52w) * 100) if (high_52w - low_52w) > 0 else 50
            
            # Performance
            change_1d = ((price - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100) if len(df) >= 2 else 0
            change_1w = ((price - df["Close"].iloc[-5]) / df["Close"].iloc[-5] * 100) if len(df) >= 5 else 0
            change_1m = ((price - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100) if len(df) >= 21 else 0
            change_3m = ((price - df["Close"].iloc[-63]) / df["Close"].iloc[-63] * 100) if len(df) >= 63 else 0
            
            # Volume analysis
            avg_vol = df["Volume"].tail(20).mean() if "Volume" in df.columns else 0
            current_vol = df["Volume"].iloc[-1] if "Volume" in df.columns else 0
            vol_ratio = (current_vol / avg_vol) if avg_vol > 0 else 1
            unusual_vol = vol_ratio > 1.5
            
            # Bollinger Bands position
            bb_upper = latest(t, "BBU_20_2.0") if "BBU_20_2.0" in df.columns else float("nan")
            bb_lower = latest(t, "BBL_20_2.0") if "BBL_20_2.0" in df.columns else float("nan")
            bb_position = "middle"
            if not pd.isna(bb_upper) and not pd.isna(bb_lower):
                if price >= bb_upper * 0.98:
                    bb_position = "upper"
                elif price <= bb_lower * 1.02:
                    bb_position = "lower"
            
            # Trend & signals
            trend = "🟢 Bullish" if (not pd.isna(sma200) and price > sma200) else "🔴 Bearish"
            signal = classify_status(price, sma200, rsi, df, fv)
            
            # MACD signal
            macd_trend = "bullish" if (not pd.isna(macd) and not pd.isna(macd_signal) and macd > macd_signal) else "bearish"
            
            # Get ticker info
            info = fetch_ticker_info(t)
            sector = info.get("sector", "N/A")
            industry = info.get("industry", "N/A")
            div_yield = info.get("dividendYield", 0) or 0
            
            # Calculate buy priority score (0-100)
            buy_score = 50  # Base score
            # RSI contribution
            if not pd.isna(rsi):
                if rsi < 30:
                    buy_score += 20
                elif rsi < 40:
                    buy_score += 10
                elif rsi > 70:
                    buy_score -= 15
            # Discount contribution
            if discount_pct > 20:
                buy_score += 20
            elif discount_pct > 10:
                buy_score += 10
            elif discount_pct < -10:
                buy_score -= 10
            # Trend contribution
            if trend == "🟢 Bullish":
                buy_score += 5
            # MACD contribution
            if macd_trend == "bullish":
                buy_score += 5
            # BB position
            if bb_position == "lower":
                buy_score += 10
            elif bb_position == "upper":
                buy_score -= 10
            # Volume surge (could indicate breakout)
            if unusual_vol and change_1d > 0:
                buy_score += 5
            
            buy_score = max(0, min(100, buy_score))  # Clamp to 0-100
            
            # Entry/Exit targets
            entry_target = price * 0.95 if signal != "BUY" else price  # Buy at 5% discount unless already BUY
            exit_target = fv if fv > price else price * 1.15  # Fair value or 15% gain
            
            wl_rows.append({
                "Ticker": t,
                "Price": price,
                "Fair_Value": fv,
                "Discount_Pct": discount_pct,
                "RSI": rsi,
                "SMA50": sma50,
                "SMA200": sma200,
                "MACD": macd,
                "MACD_Signal": macd_signal,
                "MACD_Trend": macd_trend,
                "Trend": trend,
                "Signal": signal,
                "High_52w": high_52w,
                "Low_52w": low_52w,
                "Pct_From_High": pct_from_high,
                "Range_Position": range_position,
                "Change_1D": change_1d,
                "Change_1W": change_1w,
                "Change_1M": change_1m,
                "Change_3M": change_3m,
                "Vol_Ratio": vol_ratio,
                "Unusual_Vol": unusual_vol,
                "BB_Position": bb_position,
                "Sector": sector,
                "Industry": industry,
                "Div_Yield": div_yield,
                "Buy_Score": buy_score,
                "Entry_Target": entry_target,
                "Exit_Target": exit_target,
                "Valuation": fair_val["valuation"],
            })
        
        # Sort by buy score (best opportunities first)
        wl_rows = sorted(wl_rows, key=lambda x: x["Buy_Score"], reverse=True)
        
        # ── Summary Metrics ──
        st.subheader("📊 Watchlist Overview")
        
        col1, col2, col3, col4 = st.columns(4)
        
        buy_signals = sum(1 for r in wl_rows if r["Signal"] == "BUY")
        sell_signals = sum(1 for r in wl_rows if r["Signal"] == "SELL")
        undervalued = sum(1 for r in wl_rows if r["Discount_Pct"] > 10)
        unusual_volume = sum(1 for r in wl_rows if r["Unusual_Vol"])
        
        col1.metric("🎯 Total Stocks", len(wl_rows))
        col2.metric("🟢 Buy Signals", buy_signals, delta=f"+{buy_signals}" if buy_signals > 0 else None)
        col3.metric("💰 Undervalued", undervalued, help=">10% discount to fair value")
        col4.metric("📈 Unusual Volume", unusual_volume, help=">150% of avg volume")
        
        st.divider()
        
        # ── Buy Priority Ranking ──
        st.subheader("🏆 Buy Priority Ranking")
        st.caption("Ranked by score (RSI, discount, trend, MACD, volume)")
        
        # Responsive: show top 5 in a horizontal scrollable container for mobile
        top_5 = wl_rows[:5]
        
        # Build HTML for responsive grid
        ranking_html = '<div style="display:flex; flex-wrap:wrap; gap:10px; justify-content:center">'
        for idx, row in enumerate(top_5):
            score = row["Buy_Score"]
            if score >= 70:
                score_color = "#3fb950"
                score_emoji = "🔥"
            elif score >= 50:
                score_color = "#e3b341"
                score_emoji = "✨"
            else:
                score_color = "#8b949e"
                score_emoji = "📊"
            
            ranking_html += f'''
            <div style="flex:1 1 100px; min-width:80px; max-width:150px; text-align:center; padding:10px 8px; background:#1c1f26; border-radius:10px; border:2px solid {score_color}">
                <div style="font-size:clamp(1rem, 4vw, 1.5rem); font-weight:800; color:{score_color}">{idx+1}</div>
                <div style="font-size:clamp(0.9rem, 3vw, 1.2rem); font-weight:700; color:#e6edf3">{row['Ticker']}</div>
                <div style="font-size:clamp(1.2rem, 5vw, 2rem)">{score_emoji}</div>
                <div style="font-size:clamp(0.8rem, 2.5vw, 1.1rem); color:{score_color}">{score}</div>
            </div>
            '''
        ranking_html += '</div>'
        
        st.markdown(ranking_html, unsafe_allow_html=True)
        
        st.divider()
        
        # ── Detailed Analysis Cards ──
        st.subheader("📋 Detailed Stock Analysis")
        
        # View mode toggle
        view_mode = st.radio("View Mode", ["Cards", "Table"], horizontal=True, key="wl_view_mode")
        
        if view_mode == "Table":
            # Table view
            table_data = []
            for r in wl_rows:
                table_data.append({
                    "🏆": f"{r['Buy_Score']}/100",
                    "Ticker": r["Ticker"],
                    "Price": f"${r['Price']:.2f}",
                    "Fair Value": f"${r['Fair_Value']:.2f}",
                    "Discount": f"{r['Discount_Pct']:+.1f}%",
                    "RSI": f"{r['RSI']:.1f}" if not pd.isna(r['RSI']) else "N/A",
                    "Signal": r["Signal"],
                    "Trend": r["Trend"],
                    "1D": f"{r['Change_1D']:+.1f}%",
                    "1W": f"{r['Change_1W']:+.1f}%",
                    "1M": f"{r['Change_1M']:+.1f}%",
                    "Entry": f"${r['Entry_Target']:.2f}",
                    "Target": f"${r['Exit_Target']:.2f}",
                })
            st.dataframe(table_data, use_container_width=True, hide_index=True)
        
        else:
            # Card view (detailed)
            for r in wl_rows:
                # Determine card styling based on signal
                if r["Signal"] == "BUY":
                    border_color = "#3fb950"
                    bg_color = "#0d2318"
                elif r["Signal"] == "SELL":
                    border_color = "#f85149"
                    bg_color = "#1a0d0d"
                else:
                    border_color = "#2e3140"
                    bg_color = "#1c1f26"
                
                # Buy score badge color
                score = r["Buy_Score"]
                if score >= 70:
                    score_color = "#3fb950"
                elif score >= 50:
                    score_color = "#e3b341"
                else:
                    score_color = "#8b949e"
                
                st.markdown(f"""
                <div style="
                    background:{bg_color};
                    border:2px solid {border_color};
                    border-radius:16px;
                    padding:15px;
                    margin-bottom:15px;
                ">
                    <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; margin-bottom:12px; gap:10px">
                        <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px">
                            <span style="font-size:clamp(1.1rem, 4vw, 1.5rem); font-weight:800; color:#e6edf3">{r['Ticker']}</span>
                            <span style="padding:4px 10px; background:{score_color}; border-radius:20px; font-weight:700; color:#000; font-size:0.8rem">
                                {score}/100
                            </span>
                        </div>
                        <div style="text-align:right">
                            <span style="font-size:clamp(1rem, 3.5vw, 1.3rem); font-weight:700; color:#58a6ff">${r['Price']:.2f}</span>
                            <span style="margin-left:8px; font-size:0.75rem; color:#8b949e">{r['Sector'][:15]}</span>
                        </div>
                    </div>
                    
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(80px, 1fr)); gap:8px; margin-bottom:12px">
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">Fair Val</div>
                            <div style="font-size:0.85rem; font-weight:700; color:#58a6ff">${r['Fair_Value']:.2f}</div>
                        </div>
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">Discount</div>
                            <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['Discount_Pct'] > 0 else '#f85149'}">{r['Discount_Pct']:+.1f}%</div>
                        </div>
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">RSI</div>
                            <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['RSI'] < 35 else '#f85149' if r['RSI'] > 65 else '#e3b341'}">{r['RSI']:.1f}</div>
                        </div>
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">Signal</div>
                            <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['Signal'] == 'BUY' else '#f85149' if r['Signal'] == 'SELL' else '#e3b341'}">{r['Signal']}</div>
                        </div>
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">MACD</div>
                            <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['MACD_Trend'] == 'bullish' else '#f85149'}">{'📈' if r['MACD_Trend'] == 'bullish' else '📉'}</div>
                        </div>
                        <div style="text-align:center; padding:6px; background:#161b22; border-radius:8px">
                            <div style="font-size:0.65rem; color:#8b949e">Volume</div>
                            <div style="font-size:0.85rem; font-weight:700; color:{'#e3b341' if r['Unusual_Vol'] else '#8b949e'}">{r['Vol_Ratio']:.1f}x{'🔥' if r['Unusual_Vol'] else ''}</div>
                        </div>
                    </div>
                    
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(60px, 1fr)); gap:6px; margin-bottom:12px">
                        <div style="text-align:center; padding:5px; background:#0d1117; border-radius:6px">
                            <span style="font-size:0.65rem; color:#8b949e">1D </span>
                            <span style="font-weight:700; font-size:0.8rem; color:{'#3fb950' if r['Change_1D'] >= 0 else '#f85149'}">{r['Change_1D']:+.1f}%</span>
                        </div>
                        <div style="text-align:center; padding:5px; background:#0d1117; border-radius:6px">
                            <span style="font-size:0.65rem; color:#8b949e">1W </span>
                            <span style="font-weight:700; font-size:0.8rem; color:{'#3fb950' if r['Change_1W'] >= 0 else '#f85149'}">{r['Change_1W']:+.1f}%</span>
                        </div>
                        <div style="text-align:center; padding:5px; background:#0d1117; border-radius:6px">
                            <span style="font-size:0.65rem; color:#8b949e">1M </span>
                            <span style="font-weight:700; font-size:0.8rem; color:{'#3fb950' if r['Change_1M'] >= 0 else '#f85149'}">{r['Change_1M']:+.1f}%</span>
                        </div>
                        <div style="text-align:center; padding:5px; background:#0d1117; border-radius:6px">
                            <span style="font-size:0.65rem; color:#8b949e">3M </span>
                            <span style="font-weight:700; font-size:0.8rem; color:{'#3fb950' if r['Change_3M'] >= 0 else '#f85149'}">{r['Change_3M']:+.1f}%</span>
                        </div>
                    </div>
                    
                    <div style="margin-bottom:12px">
                        <div style="display:flex; flex-wrap:wrap; justify-content:space-between; font-size:0.7rem; color:#8b949e; margin-bottom:4px; gap:5px">
                            <span>Low: ${r['Low_52w']:.2f}</span>
                            <span>52W Range</span>
                            <span>High: ${r['High_52w']:.2f}</span>
                        </div>
                        <div style="background:#161b22; border-radius:10px; height:10px; position:relative">
                            <div style="position:absolute; left:{r['Range_Position']}%; top:50%; transform:translate(-50%, -50%); width:14px; height:14px; background:{'#3fb950' if r['Range_Position'] < 30 else '#f85149' if r['Range_Position'] > 70 else '#58a6ff'}; border-radius:50%; border:2px solid #e6edf3;"></div>
                            <div style="width:{r['Range_Position']}%; height:100%; background:linear-gradient(90deg, #3fb950, #e3b341, #f85149); border-radius:10px; opacity:0.3"></div>
                        </div>
                        <div style="text-align:center; font-size:0.75rem; color:#e6edf3; margin-top:4px">
                            {r['Pct_From_High']:.1f}% from High
                        </div>
                    </div>
                    
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(90px, 1fr)); gap:10px">
                        <div style="text-align:center; padding:8px; background:#0d2318; border:1px solid #3fb950; border-radius:10px">
                            <div style="font-size:0.7rem; color:#8b949e">🎯 Entry</div>
                            <div style="font-size:1rem; font-weight:700; color:#3fb950">${r['Entry_Target']:.2f}</div>
                        </div>
                        <div style="text-align:center; padding:8px; background:#161b22; border:1px solid #58a6ff; border-radius:10px">
                            <div style="font-size:0.7rem; color:#8b949e">📊 Now</div>
                            <div style="font-size:1rem; font-weight:700; color:#58a6ff">${r['Price']:.2f}</div>
                        </div>
                        <div style="text-align:center; padding:8px; background:#1a1d0d; border:1px solid #e3b341; border-radius:10px">
                            <div style="font-size:0.7rem; color:#8b949e">🚀 Target</div>
                            <div style="font-size:1rem; font-weight:700; color:#e3b341">${r['Exit_Target']:.2f}</div>
                        </div>
                    </div>
                    
                    <div style="margin-top:10px; font-size:0.7rem; color:#6e7681; word-wrap:break-word">
                        {r['Trend']} | {r['Valuation'][:25]} {'| 💰 ' + f"{r['Div_Yield']*100:.1f}%" if r['Div_Yield'] > 0 else ''}
                    </div>
                </div>
                """, unsafe_allow_html=True)
        
        st.divider()
        
        # ── Mini Charts Section ──
        st.subheader("📈 Price Charts")
        
        selected_wl_ticker = st.selectbox("Select stock for detailed chart", [r["Ticker"] for r in wl_rows], key="wl_chart_select")
        
        if selected_wl_ticker:
            df_chart = ticker_data.get(selected_wl_ticker, pd.DataFrame())
            if not df_chart.empty:
                # Create detailed chart
                fig = go.Figure()
                
                # Candlestick
                fig.add_trace(go.Candlestick(
                    x=df_chart.index,
                    open=df_chart['Open'],
                    high=df_chart['High'],
                    low=df_chart['Low'],
                    close=df_chart['Close'],
                    name='Price'
                ))
                
                # SMA lines
                if 'SMA50' in df_chart.columns:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA50'], name='SMA50', line=dict(color='#e3b341', width=1)))
                if 'SMA200' in df_chart.columns:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA200'], name='SMA200', line=dict(color='#58a6ff', width=1)))
                
                # Bollinger Bands
                if 'BBU_20_2.0' in df_chart.columns and 'BBL_20_2.0' in df_chart.columns:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['BBU_20_2.0'], name='BB Upper', line=dict(color='#8b949e', width=1, dash='dash')))
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['BBL_20_2.0'], name='BB Lower', line=dict(color='#8b949e', width=1, dash='dash'), fill='tonexty', fillcolor='rgba(139,148,158,0.1)'))
                
                # Fair value line
                for r in wl_rows:
                    if r["Ticker"] == selected_wl_ticker:
                        fig.add_hline(y=r["Fair_Value"], line_dash="dot", line_color="#bc8ef7", annotation_text=f"Fair Value: ${r['Fair_Value']:.2f}")
                        fig.add_hline(y=r["Entry_Target"], line_dash="dot", line_color="#3fb950", annotation_text=f"Entry: ${r['Entry_Target']:.2f}")
                        fig.add_hline(y=r["Exit_Target"], line_dash="dot", line_color="#e3b341", annotation_text=f"Target: ${r['Exit_Target']:.2f}")
                        break
                
                fig.update_layout(
                    template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                    height=500,
                    title=f"{selected_wl_ticker} - Price Chart with Indicators",
                    xaxis_title="Date",
                    yaxis_title="Price ($)",
                    xaxis_rangeslider_visible=False
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # RSI chart
                if 'RSI' in df_chart.columns:
                    fig_rsi = go.Figure()
                    fig_rsi.add_trace(go.Scatter(x=df_chart.index, y=df_chart['RSI'], name='RSI', line=dict(color='#bc8ef7', width=2)))
                    fig_rsi.add_hline(y=70, line_dash="dash", line_color="#f85149", annotation_text="Overbought")
                    fig_rsi.add_hline(y=30, line_dash="dash", line_color="#3fb950", annotation_text="Oversold")
                    fig_rsi.update_layout(template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white", height=200, title="RSI Indicator", yaxis_range=[0, 100])
                    st.plotly_chart(fig_rsi, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 – Portfolio Analytics
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("📊 Portfolio Analytics")
    
    if not portfolio_positions:
        st.info("Voeg stocks toe aan je portfolio om analytics te zien.")
    else:
        # Calculate portfolio metrics
        metrics = calculate_portfolio_metrics(portfolio_positions, ticker_data)
        
        # Display key metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("💰 Totaal Geïnvesteerd", f"$ {metrics.get('total_invested', 0):,.2f}")
        col2.metric("📈 Huidige Waarde", f"$ {metrics.get('total_current', 0):,.2f}")
        col3.metric("💹 Totaal Return", f"$ {metrics.get('total_return', 0):+,.2f}")
        col4.metric("📊 Return %", f"{metrics.get('total_return_pct', 0):+.2f}%")
        
        st.divider()
        
        # Sector allocation
        st.subheader("🏢 Sector Allocation")
        sectors = metrics.get('sectors', {})
        if sectors:
            sector_df = pd.DataFrame({
                'Sector': list(sectors.keys()),
                'Waarde ($)': list(sectors.values())
            })
            sector_df['Percentage'] = (sector_df['Waarde ($)'] / sector_df['Waarde ($)'].sum() * 100).round(1)
            
            col1, col2 = st.columns([2, 1])
            with col1:
                fig = go.Figure(data=[go.Pie(
                    labels=sector_df['Sector'], 
                    values=sector_df['Waarde ($)'],
                    hole=0.4,
                    marker=dict(colors=["#3fb950", "#f85149", "#58a6ff", "#e3b341", "#bc8ef7", "#79c0ff", "#f0883e", "#db61a2"])
                )])
                fig.update_layout(
                    template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                    height=400
                )
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.dataframe(sector_df[['Sector', 'Percentage']], use_container_width=True, hide_index=True)
        
        st.divider()
        
        # Correlation Matrix
        if len(portfolio_positions) > 1:
            st.subheader("🔗 Correlation Matrix")
            st.caption("Lage correlatie = betere diversificatie")
            
            prices_corr = {}
            for pos in portfolio_positions[:10]:
                df_temp = ticker_data.get(pos["ticker"], pd.DataFrame())
                if not df_temp.empty and len(df_temp) >= 60:
                    prices_corr[pos["ticker"]] = df_temp["Close"].pct_change().dropna()
            
            if len(prices_corr) > 1:
                corr_df = pd.DataFrame(prices_corr)
                corr = corr_df.corr()
                
                fig_corr = go.Figure(data=go.Heatmap(
                    z=corr.values,
                    x=corr.columns,
                    y=corr.columns,
                    colorscale='RdBu_r',
                    zmid=0,
                    zmin=-1,
                    zmax=1,
                    text=[[f"{v:.2f}" for v in row] for row in corr.values],
                    texttemplate="%{text}",
                    textfont=dict(size=11),
                ))
                fig_corr.update_layout(
                    template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                    height=400,
                    margin=dict(l=10, r=10, t=10, b=10)
                )
                st.plotly_chart(fig_corr, use_container_width=True)
                
                # Diversification score
                avg_corr = corr.values[np.triu_indices_from(corr.values, k=1)].mean()
                if avg_corr < 0.3:
                    div_score = "🟢 Uitstekend gediversifieerd"
                elif avg_corr < 0.5:
                    div_score = "🟡 Redelijk gediversifieerd"
                elif avg_corr < 0.7:
                    div_score = "🟠 Matig gediversifieerd"
                else:
                    div_score = "🔴 Slecht gediversifieerd - stocks bewegen samen"
                
                st.metric("📊 Gem. Correlatie", f"{avg_corr:.2f}", delta=div_score)
        
        st.divider()
        
        # Risk Analysis
        st.subheader("⚠️ Risk Assessment")
        
        volatilities = []
        for pos in portfolio_positions:
            df = ticker_data.get(pos["ticker"], pd.DataFrame())
            if not df.empty and len(df) >= 20:
                returns = df["Close"].pct_change()
                vol = returns.std() * 100
                volatilities.append({"ticker": pos["ticker"], "volatility": vol})
        
        if volatilities:
            avg_volatility = sum(v["volatility"] for v in volatilities) / len(volatilities)
            if avg_volatility < 2:
                risk_level = "🟢 Low Risk"
            elif avg_volatility < 4:
                risk_level = "🟡 Medium Risk"
            else:
                risk_level = "🔴 High Risk"
            
            col1, col2 = st.columns(2)
            col1.metric("📈 Portfolio Volatility", f"{avg_volatility:.2f}%")
            col2.metric("⚙️ Risk Level", risk_level)
            
            # Per-stock volatility chart
            vol_df = pd.DataFrame(volatilities).sort_values("volatility", ascending=True)
            fig_vol = go.Figure(go.Bar(
                x=vol_df["volatility"],
                y=vol_df["ticker"],
                orientation="h",
                marker_color=["#3fb950" if v < 2 else "#e3b341" if v < 4 else "#f85149" for v in vol_df["volatility"]],
                text=[f"{v:.2f}%" for v in vol_df["volatility"]],
                textposition="outside",
            ))
            fig_vol.update_layout(
                template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                height=max(200, len(volatilities) * 35),
                margin=dict(l=10, r=50, t=10, b=10),
                xaxis_title="Daily Volatility %",
                showlegend=False,
            )
            st.plotly_chart(fig_vol, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 – Advanced Analysis
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("⚙️ Advanced Technical Analysis")
    st.info("""
    ⚙️ **Wat zie je hier?**  
    Diepgaande technische analyse per stock: Support/Resistance levels, volume analyse, 
    fundamentele gezondheid (P/E, ROE, Debt/Equity, Profit Margin) en correlatie matrix tussen je stocks.
    """)
    
    if not all_tickers:
        st.info("Voeg tickers toe om advanced analysis te zien.")
    else:
        selected_ticker_adv = st.selectbox(
            "Selecteer aandeel voor gedetailleerde analyse",
            options=all_tickers,
            key="adv_ticker_select"
        )
        
        if selected_ticker_adv:
            df_adv = ticker_data.get(selected_ticker_adv, pd.DataFrame())
            
            if not df_adv.empty:
                # Get info
                info = fetch_ticker_info(selected_ticker_adv)
                sr = calculate_support_resistance(df_adv)
                current_price = df_adv["Close"].iloc[-1]
                
                # Display info
                st.subheader(f"📋 {selected_ticker_adv} - Gedetailleerde Informatie")
                
                # Technical metrics
                info_cols = st.columns(4)
                info_cols[0].metric("💰 Sektor", info.get("sector", "N/A"))
                info_cols[1].metric("🏭 Industrie", info.get("industry", "N/A"))
                info_cols[2].metric("💵 P/E Ratio", f"{info.get('pe_ratio', 'N/A')}")
                info_cols[3].metric("📊 Dividend Yield", f"{info.get('dividend_yield', 0)*100:.2f}%")
                
                st.divider()
                
                # Fundamental metrics
                st.subheader("📈 Fundamentale Gezondheid")
                
                fund_cols = st.columns(5)
                
                # Earnings growth
                eg = info.get("earnings_growth")
                eg_str = f"{eg*100:+.1f}%" if eg is not None else "N/A"
                eg_color = "🟢" if eg and eg > 0 else ("🔴" if eg and eg < 0 else "⚪")
                fund_cols[0].metric("📊 Earnings Growth", eg_str, delta=eg_color)
                
                # Revenue growth
                rg = info.get("revenue_growth")
                rg_str = f"{rg*100:+.1f}%" if rg is not None else "N/A"
                rg_color = "🟢" if rg and rg > 0 else ("🔴" if rg and rg < 0 else "⚪")
                fund_cols[1].metric("💹 Revenue Growth", rg_str, delta=rg_color)
                
                # ROE
                roe = info.get("roe")
                roe_str = f"{roe*100:.1f}%" if roe is not None else "N/A"
                roe_status = "🟢 Excellent" if roe and roe > 0.15 else ("🟡 Good" if roe and roe > 0.10 else "🔴")
                fund_cols[2].metric("📈 ROE", roe_str, delta=roe_status)
                
                # Debt/Equity
                de = info.get("debt_to_equity")
                de_str = f"{de:.2f}x" if de is not None else "N/A"
                de_status = "🟢 Safe" if de and de < 0.5 else ("🟡 OK" if de and de < 1.5 else "🔴 High")
                fund_cols[3].metric("💳 Debt/Equity", de_str, delta=de_status)
                
                # Profit Margin
                pm = info.get("profit_margin")
                pm_str = f"{pm*100:+.1f}%" if pm is not None else "N/A"
                pm_status = "🟢" if pm and pm > 0.15 else ("🟡" if pm and pm > 0 else "🔴")
                fund_cols[4].metric("💰 Profit Margin", pm_str, delta=pm_status)
                
                st.divider()
                
                # Support & Resistance
                st.subheader("📍 Support & Resistance Levels")
                
                sr_cols = st.columns(5)
                sr_cols[0].metric("🔴 Resistance 2", f"$ {sr.get('resistance2', 0):.2f}")
                sr_cols[1].metric("🟠 Resistance 1", f"$ {sr.get('resistance1', 0):.2f}")
                sr_cols[2].metric("🟡 Pivot Point", f"$ {sr.get('pivot', 0):.2f}")
                sr_cols[3].metric("🟢 Support 1", f"$ {sr.get('support1', 0):.2f}")
                sr_cols[4].metric("🔵 Support 2", f"$ {sr.get('support2', 0):.2f}")
                
                st.divider()
                
                # Volume Analysis
                st.subheader("📊 Volume Analysis")
                vol_trend = calculate_volume_trend(df_adv)
                st.info(f"Volume Trend: {vol_trend}")
                
                # Volume chart
                if len(df_adv) > 0:
                    fig_vol = go.Figure(data=[
                        go.Bar(x=df_adv.index[-30:], y=df_adv["Volume"].tail(30), name="Volume",
                               marker_color=["#3fb950" if df_adv["Close"].iloc[i] >= df_adv["Open"].iloc[i] else "#f85149" 
                                            for i in range(-30, 0)])
                    ])
                    fig_vol.update_layout(
                        template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                        height=300, title="30-Day Volume"
                    )
                    st.plotly_chart(fig_vol, use_container_width=True)
                
                st.divider()
                
                # Full Technical Chart
                st.subheader("📈 Technische Chart")
                fig_tech = build_enhanced_chart(df_adv, selected_ticker_adv, show_bb=True, show_macd=True)
                if st.session_state.theme == "light":
                    fig_tech.update_layout(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff")
                st.plotly_chart(fig_tech, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 – Stock Recommendations
# ════════════════════════════════════════════════════════════════════════════
with tab5:
    st.header("🎯 Stock Aanbevelingen")
    st.info("""
    🎯 **Wat zie je hier?**  
    AI-gegenereerde koopaanbevelingen uit 60+ populaire stocks. Score van 0-20 gebaseerd op:
    Fair Value (ondergewaardeerd?), RSI (oversold?), trend, volume, en fundamentele data.
    ✅ = je hebt deze al in portfolio.
    """)
    
    # Get market health
    market_health = get_market_health()
    
    # Display market context
    st.subheader("📊 Marktomgeving")
    market_cols = st.columns(3)
    market_cols[0].metric("📈 VIX Index", f"{market_health.get('vix', 'N/A'):.1f}" if not pd.isna(market_health.get('vix')) else "N/A")
    market_cols[1].metric("🎯 Market Regime", market_health.get("market_regime", "N/A"))
    market_cols[2].metric("📊 Bull/Bear", market_health.get("bull_regime", "N/A"))
    
    st.divider()
    
    # Get portfolio tickers
    portfolio_tickers = [p["ticker"] for p in portfolio_positions]
    
    # Get recommendations (works even without watchlist - uses popular stocks)
    recommendations = get_stock_recommendations(watchlist_tickers, portfolio_tickers, ticker_data)
    
    if not recommendations:
        st.info("💡 Geen aanbevelingen op dit moment. De markt wacht op betere koopkansen. Check later opnieuw!")
    else:
        st.subheader(f"🎯 Top {len(recommendations)} Enterprise-Grade Aanbevelingen")
        
        # Summary metrics
        summary_cols = st.columns(4)
        summary_cols[0].metric("📊 Aanbevelingen", len(recommendations))
        summary_cols[1].metric("📈 Gem. Upside", f"{sum([r['upside'] for r in recommendations])/len(recommendations):.1f}%")
        summary_cols[2].metric("⭐ Avg. Score", f"{sum([r['score'] for r in recommendations])/len(recommendations):.1f}/20")
        
        # Count owned stocks
        owned_count = sum(1 for r in recommendations if r.get("owned", False))
        summary_cols[3].metric("✅ Al in Portfolio", f"{owned_count} van {len(recommendations)}")
        
        st.divider()
        
        # Detailed recommendations
        for i, rec in enumerate(recommendations, 1):
            with st.container():
                # Header with rating + owned indicator
                col_header1, col_header2, col_header3 = st.columns([2, 1, 1])
                owned_badge = "✅ IN PORTFOLIO" if rec.get("owned", False) else ""
                col_header1.markdown(f"### {i}. {rec['ticker']} {rec['rating']} {owned_badge}")
                col_header2.metric("Score", f"{rec['score']}/20")
                col_header3.metric("Upside", f"+{rec['upside']:.1f}%")
                
                # Key metrics
                metric_cols = st.columns(6)
                metric_cols[0].metric("💰 Prijs", f"${rec['price']:.2f}")
                metric_cols[1].metric("🎯 Fair Value", f"${rec['fair_value']:.2f}")
                metric_cols[2].metric("📊 RSI", f"{rec['rsi']:.1f}" if not pd.isna(rec['rsi']) else "N/A")
                metric_cols[3].metric("📈 P/E", f"{rec['pe_ratio']:.1f}x" if rec['pe_ratio'] else "N/A")
                metric_cols[4].metric("🛑 Stop Loss", f"${rec['stop_loss']:.2f}")
                metric_cols[5].metric("Risk", f"{rec['stop_loss_pct']:.1f}%")
                
                # Reliability badge
                reliability = rec.get("reliability", "Unknown")
                reliability_map = {
                    "Good Confidence": ("🟢", "Excellent"),
                    "Moderate Confidence": ("🟡", "Good"),
                    "High Uncertainty": ("🔴", "Risky"),
                    "EXTREME": ("⚠️", "Unreliable")
                }
                
                # Find matching reliability
                rel_badge = "❓"
                rel_label = "Unknown"
                for key, (badge, label) in reliability_map.items():
                    if key in reliability:
                        rel_badge = badge
                        rel_label = label
                        break
                
                st.markdown(f"""
                **Sector:** {rec.get('sector', 'Unknown')} | **Betrouwbaarheid:** {rel_badge} {rel_label}
                
                **Redenen:**
                {rec['reasons']}
                """)
                
                # Action buttons
                action_cols = st.columns([1, 1, 1, 2])
                action_cols[0].button(f"📌 Bekijk {rec['ticker']}", key=f"view_{rec['ticker']}")
                action_cols[1].button(f"⭐ Watchlist", key=f"watch_{rec['ticker']}")
                action_cols[2].button(f"💼 Koop", key=f"buy_{rec['ticker']}")
                action_cols[3].markdown("")
                
                st.divider()
        
        # Risk disclaimer
        st.warning("""
        ⚠️ **DISCLAIMER:** 
        - Deze aanbevelingen zijn gebaseerd op technische analyse EN fundamentale data
        - Always do your own research (DYOR) voordat je koopt
        - Stop loss levels zijn suggesties, geen garanties
        - Portefeuillediversificatie is essentieel
        - Past performance is geen garantie voor toekomstige resultaten
        - Market conditions (VIX, sector rotation) kunnen snel veranderen
        """)


# ════════════════════════════════════════════════════════════════════════════
# TAB 6 – Alerts & Tax Loss Harvesting
# ════════════════════════════════════════════════════════════════════════════
with tab6:
    st.header("⚠️ Portfolio Alerts & Tax Benefits")
    st.info("""
    ⚠️ **Wat zie je hier?**  
    **Alerts**: Waarschuwingen voor grote verliezen (>10%), grote winsten (>30%) en RSI extremen.  
    **Tax Loss Harvesting**: Verliesgevende posities die je kunt verkopen voor belastingvoordeel (24% bracket).
    """)
    
    # Alerts
    st.subheader("🚨 Active Alerts")
    alerts = get_position_alerts(portfolio_positions, ticker_data)
    
    if not alerts:
        st.success("✅ Geen alerts - alles ziet er goed uit!")
    else:
        for alert in alerts:
            if alert["severity"] == "high":
                st.error(f"{alert['type']}: {alert['message']}")
            elif alert["severity"] == "medium":
                st.warning(f"{alert['type']}: {alert['message']}")
            else:
                st.info(f"{alert['type']}: {alert['message']}")
    
    st.divider()
    
    # Tax Loss Harvesting
    st.subheader("💰 Tax Loss Harvesting Opportunities")
    st.caption("Verkoop verliesgevende posities om belastingvoordeel te krijgen")
    
    tax_losses = calculate_tax_loss_harvesting(portfolio_positions, ticker_data)
    
    if not tax_losses:
        st.info("🎉 Geen verliesgevende posities - goed gedaan!")
    else:
        total_tax_benefit = sum(tl["tax_benefit"] for tl in tax_losses)
        st.metric("💵 Potentieel belastingvoordeel", f"$ {total_tax_benefit:,.2f}", help="Bij 24% belastingtarief")
        
        st.divider()
        
        for tl in tax_losses:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric(tl["ticker"], f"${tl['total_loss']:+,.2f}")
            col2.metric("Loss %", f"{tl['loss_pct']:+.1f}%")
            col3.metric("Shares", f"{tl['shares']:.2f}")
            col4.metric("Tax Benefit", f"${tl['tax_benefit']:,.0f}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 7 – Rebalancing & Risk Metrics
# ════════════════════════════════════════════════════════════════════════════
with tab7:
    st.header("💡 Rebalancing & Risk Management")
    st.info("""
    💡 **Wat zie je hier?**  
    **Risk Metrics**: VaR (Value at Risk), Sharpe Ratio, Max Drawdown, Beta vs S&P 500.  
    **Rebalancing**: Suggesties om je portfolio te herbalanceren naar target sector allocaties.
    """)
    
    # Risk Metrics
    st.subheader("📊 Risk Metrics (Professional Grade)")
    
    risk_metrics = calculate_risk_metrics(portfolio_positions, ticker_data)
    
    if risk_metrics:
        metrics_cols = st.columns(5)
        
        var_95 = risk_metrics.get("var_95", 0)
        metrics_cols[0].metric(
            "📉 Value at Risk (95%)",
            f"{var_95:.2%}",
            help="Worst 5% daily loss scenario"
        )
        
        sharpe = risk_metrics.get("sharpe_ratio", 0)
        metrics_cols[1].metric(
            "📈 Sharpe Ratio",
            f"{sharpe:.2f}",
            help="Return vs Risk trade-off (>1 is good)"
        )
        
        max_dd = risk_metrics.get("max_drawdown", 0)
        metrics_cols[2].metric(
            "📉 Max Drawdown",
            f"{max_dd:.2%}",
            help="Worst historical loss"
        )
        
        beta = risk_metrics.get("beta", 0)
        if not pd.isna(beta):
            metrics_cols[3].metric(
                "📊 Beta (vs S&P 500)",
                f"{beta:.2f}",
                help="<1 less volatile, >1 more volatile"
            )
        
        ann_vol = risk_metrics.get("annualized_volatility", 0)
        metrics_cols[4].metric(
            "🎢 Annualized Volatility",
            f"{ann_vol:.2%}",
            help="Expected yearly price swings"
        )
        
        st.divider()
        
        # Interpretation
        st.info(f"""
        **Risk Interpretation:**
        - **Sharpe Ratio**: {sharpe:.2f} {'✅ Good' if sharpe > 1 else '⚠️ Mediocre'} - You're getting {sharpe:.2f} units of return per unit of risk
        - **Max Drawdown**: {max_dd:.1%} - Worst your portfolio could have fallen
        - **Beta**: {beta:.2f} {'✅ Stable' if beta < 1 else '⚠️ Volatile'} - {'Less' if beta < 1 else 'More'} volatile than market
        """)
    else:
        st.warning("Not enough data for risk metrics yet (need 60+ days of history)")
    
    st.divider()
    
    # Rebalancing
    st.subheader("⚖️ Portfolio Rebalancing Suggestions")
    
    rebalance = get_rebalancing_suggestion(portfolio_positions, ticker_data)
    
    if rebalance and rebalance.get("rebalancing"):
        st.caption("Sectors that are over/under-weighted relative to target allocation")
        
        for rec in rebalance["rebalancing"]:
            col1, col2, col3 = st.columns([2, 2, 1])
            col1.metric(
                f"{rec['sector']}",
                f"{rec['current']:.1f}%",
                delta=f"{rec['target']:.1f}% target"
            )
            col2.markdown(f"{rec['action']}")
            col3.metric("Amount", f"$ {rec['amount']:,.0f}")
    else:
        st.success("✅ Portfolio is well-balanced!")


# ════════════════════════════════════════════════════════════════════════════
# TAB 8 – Income & Benchmark Comparison
# ════════════════════════════════════════════════════════════════════════════
with tab8:
    st.header("💰 Income & Performance")
    st.info("""
    💰 **Wat zie je hier?**  
    **Dividend Income**: Hoeveel dividend je jaarlijks ontvangt van je portfolio.  
    **Benchmark**: Vergelijk je rendement met de S&P 500 - presteren je beter of slechter dan de markt?
    """)
    
    # Dividend Income
    st.subheader("💵 Dividend Income")
    
    dividend_info = get_dividend_info(portfolio_positions, ticker_data)
    
    if dividend_info.get("dividends"):
        st.metric(
            "📊 Annual Dividend Income",
            f"$ {dividend_info['total_annual_income']:,.2f}",
            help=f"From {dividend_info['count']} dividend-paying stocks"
        )
        
        st.divider()
        
        # Dividend breakdown
        for div in dividend_info["dividends"]:
            col1, col2, col3 = st.columns(3)
            col1.metric(div["ticker"], f"{div['yield']:.2f}%")
            col2.metric("Annual", f"$ {div['annual_dividend']:,.2f}")
            col3.metric("Position Value", f"$ {div['position_value']:,.2f}")
    else:
        st.info("No dividend-paying stocks in your portfolio yet")
    
    st.divider()
    
    # Benchmark Comparison
    st.subheader("🏆 Performance vs S&P 500")
    
    benchmark = compare_vs_benchmark(portfolio_positions, ticker_data)
    
    if benchmark:
        col1, col2, col3 = st.columns(3)
        
        port_ret = benchmark.get("portfolio_return", 0)
        sp500_ret = benchmark.get("sp500_return", 0)
        outperf = benchmark.get("outperformance", 0)
        
        col1.metric(
            "📈 Your Return (1Y)",
            f"{port_ret:+.2f}%",
            delta_color="normal"
        )
        
        col2.metric(
            "📊 S&P 500 Return",
            f"{sp500_ret:+.2f}%" if not pd.isna(sp500_ret) else "N/A",
            label_visibility="visible"
        )
        
        if not pd.isna(outperf):
            color = "normal" if outperf >= 0 else "inverse"
            col3.metric(
                "🎯 Outperformance",
                f"{outperf:+.2f}%",
                delta_color=color
            )
        
        # Interpretation
        if not pd.isna(outperf):
            if outperf > 5:
                st.success(f"🎉 Beating the market by {outperf:.1f}%! Great job!")
            elif outperf > 0:
                st.info(f"✅ Ahead of S&P 500 by {outperf:.1f}%")
            elif outperf > -5:
                st.warning(f"⚠️ Underperforming by {abs(outperf):.1f}% - consider rebalancing")
            else:
                st.error(f"❌ Significantly underperforming ({outperf:.1f}%) - review strategy")


# ════════════════════════════════════════════════════════════════════════════
# TAB 9 – Price Alerts
# ════════════════════════════════════════════════════════════════════════════
with tab9:
    st.header("🔔 Price Alerts")
    st.info("""
    🔔 **Wat zie je hier?**  
    Stel prijs alerts in voor elke stock. Kies een target prijs en of je een melding wilt 
    wanneer de prijs **boven** (koop alert) of **onder** (stop-loss alert) de target komt.
    **Email alerts** worden automatisch verzonden als je email is geconfigureerd.
    """)
    
    # Load existing alerts
    price_alerts = user_data.get("price_alerts", [])
    email_config = user_data.get("email_config", {})
    
    # Check triggered alerts
    triggered = check_price_alerts(price_alerts, ticker_data)
    
    if triggered:
        st.subheader("🚨 Triggered Alerts!")
        for alert in triggered:
            st.success(f"{alert['type']}: {alert['message']}")
        
        # Email button for triggered alerts
        if email_config.get("recipient_email"):
            if st.button("📧 Verstuur Alerts via Email", use_container_width=True, key="send_triggered_email"):
                email_body = build_alert_email_body(triggered, ticker_data)
                success, message = send_email_alert(
                    email_config["recipient_email"],
                    f"🚨 Stock Alert: {len(triggered)} alerts getriggerd!",
                    email_body,
                    email_config
                )
                if success:
                    st.success(message)
                else:
                    st.error(message)
        else:
            st.caption("💡 Configureer je email in de sidebar om alerts te ontvangen")
        
        st.divider()
    
    # Add new alert
    st.subheader("➕ Nieuwe Alert Toevoegen")
    
    alert_cols = st.columns([2, 1, 1, 1])
    with alert_cols[0]:
        alert_ticker = st.selectbox(
            "Stock",
            options=all_tickers if all_tickers else ["AAPL"],
            key="alert_ticker"
        )
    with alert_cols[1]:
        current_price_alert = latest(alert_ticker, "Close") if alert_ticker else 0
        st.metric("Huidige Prijs", f"${current_price_alert:.2f}")
    with alert_cols[2]:
        alert_target = st.number_input(
            "Target Prijs",
            value=float(current_price_alert * 1.1),
            min_value=0.01,
            step=0.01,
            key="alert_target"
        )
    with alert_cols[3]:
        alert_type = st.selectbox(
            "Alert Type",
            options=["above", "below"],
            format_func=lambda x: "📈 Boven" if x == "above" else "📉 Onder",
            key="alert_type"
        )
    
    if st.button("🔔 Alert Toevoegen", use_container_width=True):
        new_alert = {
            "ticker": alert_ticker,
            "target_price": alert_target,
            "type": alert_type,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        price_alerts.append(new_alert)
        user_data["price_alerts"] = price_alerts
        save_user_portfolio(st.session_state.current_user, user_data)
        st.success(f"✅ Alert toegevoegd voor {alert_ticker} @ ${alert_target:.2f}")
        st.rerun()
    
    st.divider()
    
    # Show existing alerts
    st.subheader("📋 Actieve Alerts")
    
    if not price_alerts:
        st.info("Nog geen alerts ingesteld")
    else:
        for i, alert in enumerate(price_alerts):
            col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
            col1.write(f"**{alert['ticker']}**")
            col2.write(f"Target: ${alert['target_price']:.2f}")
            col3.write(f"{'📈 Boven' if alert['type'] == 'above' else '📉 Onder'}")
            if col4.button("🗑️", key=f"del_alert_{i}"):
                price_alerts.pop(i)
                user_data["price_alerts"] = price_alerts
                save_user_portfolio(st.session_state.current_user, user_data)
                st.rerun()
    
    st.divider()
    
    # Email Actions Section
    st.subheader("📧 Email Acties")
    
    email_status = "✅ Geconfigureerd" if email_config.get("recipient_email") else "❌ Niet geconfigureerd"
    recipient = email_config.get("recipient_email", "Geen")
    
    _email_bg = "#161b22" if st.session_state.theme == "dark" else "#f0f2f5"
    st.markdown(f"""
    <div style="background:{_email_bg}; padding:15px; border-radius:10px; margin-bottom:15px">
        <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px">
            <div><b>Email Status:</b> {email_status}</div>
            <div><b>Ontvanger:</b> {recipient}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    email_action_cols = st.columns(2)
    
    with email_action_cols[0]:
        if st.button("📊 Verstuur Portfolio Overzicht", use_container_width=True, key="send_portfolio_email"):
            if email_config.get("recipient_email") and email_config.get("sender_email"):
                email_body = build_portfolio_summary_email(portfolio_positions, ticker_data)
                success, message = send_email_alert(
                    email_config["recipient_email"],
                    f"📊 Portfolio Overzicht - {datetime.now().strftime('%d %b %Y')}",
                    email_body,
                    email_config
                )
                if success:
                    st.success(message)
                else:
                    st.error(message)
            else:
                st.error("⚠️ Configureer eerst je email in de sidebar")
    
    with email_action_cols[1]:
        if st.button("🔔 Verstuur Alle Actieve Alerts", use_container_width=True, key="send_all_alerts_email"):
            if email_config.get("recipient_email") and email_config.get("sender_email"):
                if price_alerts:
                    # Build summary of all active alerts
                    body = "<h2>📋 Actieve Price Alerts</h2>"
                    for alert in price_alerts:
                        current_p = latest(alert["ticker"], "Close")
                        diff = ((current_p - alert["target_price"]) / alert["target_price"] * 100) if alert["target_price"] > 0 else 0
                        body += f"""
                        <div style="background:#161b22; padding:12px; border-radius:8px; margin:8px 0; border-left:4px solid {'#3fb950' if alert['type'] == 'above' else '#f85149'}">
                            <b style="font-size:16px">{alert['ticker']}</b><br>
                            <b>Type:</b> {'📈 Boven' if alert['type'] == 'above' else '📉 Onder'}<br>
                            <b>Target:</b> ${alert['target_price']:.2f}<br>
                            <b>Huidige prijs:</b> ${current_p:.2f} ({diff:+.1f}% van target)
                        </div>
                        """
                    
                    success, message = send_email_alert(
                        email_config["recipient_email"],
                        f"📋 Actieve Alerts Overzicht - {len(price_alerts)} alerts",
                        body,
                        email_config
                    )
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                else:
                    st.info("Geen actieve alerts om te versturen")
            else:
                st.error("⚠️ Configureer eerst je email in de sidebar")


# ════════════════════════════════════════════════════════════════════════════
# TAB 10 – Portfolio History & Export
# ════════════════════════════════════════════════════════════════════════════
with tab10:
    st.header("📈 Portfolio History & Export")
    st.info("""
    📈 **Wat zie je hier?**  
    **History**: Grafiek van je portfolio waarde over tijd (dagelijks bijgehouden).  
    **Export**: Download je portfolio als CSV. **News**: Recente nieuwsartikelen per stock.  
    **Earnings Calendar**: Wanneer komen earnings aan voor je stocks?
    """)
    
    # Record today's snapshot
    portfolio_history = user_data.get("portfolio_history", [])
    portfolio_history = record_portfolio_snapshot(portfolio_positions, ticker_data, portfolio_history)
    user_data["portfolio_history"] = portfolio_history
    save_user_portfolio(st.session_state.current_user, user_data)
    
    # History Chart
    st.subheader("📊 Portfolio Waarde Over Tijd")
    
    if len(portfolio_history) > 1:
        history_df = pd.DataFrame(portfolio_history)
        history_df["date"] = pd.to_datetime(history_df["date"])
        
        fig_history = go.Figure()
        fig_history.add_trace(go.Scatter(
            x=history_df["date"],
            y=history_df["value"],
            name="Portfolio Waarde",
            fill="tozeroy",
            line=dict(color="#3fb950", width=2),
            fillcolor="rgba(63,185,80,0.2)"
        ))
        fig_history.add_trace(go.Scatter(
            x=history_df["date"],
            y=history_df["cost"],
            name="Geïnvesteerd",
            line=dict(color="#58a6ff", width=2, dash="dash")
        ))
        
        fig_history.update_layout(
            template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
            height=400,
            title="Portfolio Performance",
            xaxis_title="Datum",
            yaxis_title="Waarde ($)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig_history, use_container_width=True)
        
        # Performance metrics
        if len(portfolio_history) >= 2:
            first_val = portfolio_history[0]["value"]
            last_val = portfolio_history[-1]["value"]
            total_return = ((last_val - first_val) / first_val * 100) if first_val > 0 else 0
            
            metric_cols = st.columns(4)
            metric_cols[0].metric("Start Waarde", f"${first_val:,.2f}")
            metric_cols[1].metric("Huidige Waarde", f"${last_val:,.2f}")
            metric_cols[2].metric("Totaal Rendement", f"{total_return:+.2f}%")
            metric_cols[3].metric("Dagen Gevolgd", f"{len(portfolio_history)}")
    else:
        st.info("📊 Portfolio history wordt vanaf vandaag bijgehouden. Kom morgen terug voor de grafiek!")
    
    st.divider()
    
    # Export Section
    st.subheader("📥 Export Portfolio")
    
    export_cols = st.columns(2)
    
    with export_cols[0]:
        st.markdown("**CSV Export**")
        st.caption("Download je portfolio als spreadsheet")
        
        if portfolio_positions:
            csv_data = export_portfolio_csv(portfolio_positions, ticker_data)
            st.download_button(
                label="📄 Download CSV",
                data=csv_data,
                file_name=f"portfolio_{st.session_state.current_user}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("Voeg stocks toe om te exporteren")
    
    with export_cols[1]:
        st.markdown("**Portfolio Summary**")
        st.caption("Kopieer-klare samenvatting")
        
        if portfolio_positions:
            summary_lines = [f"Portfolio {st.session_state.current_user} - {datetime.now().strftime('%Y-%m-%d')}"]
            summary_lines.append("-" * 40)
            
            total_val = 0
            total_cost = 0
            for pos in portfolio_positions:
                df = ticker_data.get(pos["ticker"], pd.DataFrame())
                if not df.empty:
                    price = df["Close"].iloc[-1]
                    val = pos["qty"] * price
                    cost = pos["qty"] * pos["gak"]
                    pnl = val - cost
                    total_val += val
                    total_cost += cost
                    summary_lines.append(f"{pos['ticker']}: {pos['qty']} shares @ ${price:.2f} = ${val:.2f} ({pnl:+.2f})")
            
            summary_lines.append("-" * 40)
            summary_lines.append(f"Total: ${total_val:,.2f} (P/L: ${total_val-total_cost:+,.2f})")
            
            st.text_area("Summary", value="\n".join(summary_lines), height=200)
    
    st.divider()
    
    # News Section
    st.subheader("📰 Recent News")
    
    news_ticker = st.selectbox(
        "Selecteer stock voor nieuws",
        options=all_tickers if all_tickers else ["AAPL"],
        key="news_ticker"
    )
    
    if news_ticker:
        news = fetch_stock_news(news_ticker)
        
        if news:
            for article in news:
                with st.container():
                    st.markdown(f"""
                    **{article['title']}**  
                    📰 {article['publisher']} | 📅 {article['published']}  
                    [Lees meer]({article['link']})
                    """)
                    st.divider()
        else:
            st.info(f"Geen recent nieuws gevonden voor {news_ticker}")
    
    st.divider()
    
    # Earnings Calendar
    st.subheader("📅 Earnings Calendar")
    
    earnings_data = []
    for pos in portfolio_positions[:10]:  # Limit for performance
        earnings = get_earnings_calendar(pos["ticker"])
        if earnings.get("earnings_date"):
            earnings_data.append({
                "Ticker": pos["ticker"],
                "Earnings Date": earnings["earnings_date"],
                "Days Until": earnings["days_until"]
            })
    
    if earnings_data:
        earnings_df = pd.DataFrame(earnings_data)
        earnings_df = earnings_df.sort_values("Days Until", key=lambda x: pd.to_numeric(x, errors='coerce'))
        
        for _, row in earnings_df.iterrows():
            days = row["Days Until"]
            if isinstance(days, (int, float)) and days <= 7:
                st.warning(f"⚠️ **{row['Ticker']}** earnings in {days} dagen! ({row['Earnings Date']})")
            elif isinstance(days, (int, float)) and days <= 30:
                st.info(f"📅 **{row['Ticker']}** earnings op {row['Earnings Date']} ({days} dagen)")
            else:
                st.write(f"📆 **{row['Ticker']}** earnings: {row['Earnings Date']}")
    else:
        st.info("Geen earnings data beschikbaar voor je portfolio stocks")


# ════════════════════════════════════════════════════════════════════════════
# TAB 11 – Position Size Calculator
# ════════════════════════════════════════════════════════════════════════════
with tab11:
    st.header("🧮 Position Size Calculator")
    st.info("""
    🧮 **Wat zie je hier?**  
    Bereken hoeveel shares je moet kopen gebaseerd op risicomanagement.  
    **Risk-Based**: Hoeveel % van je portfolio wil je riskeren per trade?  
    **Kelly Criterion**: Wiskundige formule voor optimale positiegrootte (agressiever).
    """)
    
    # Portfolio value
    total_portfolio_value = sum(
        pos["qty"] * ticker_data.get(pos["ticker"], pd.DataFrame())["Close"].iloc[-1]
        for pos in portfolio_positions
        if not ticker_data.get(pos["ticker"], pd.DataFrame()).empty
    )
    
    st.subheader("💰 Account Settings")
    
    calc_cols = st.columns(2)
    with calc_cols[0]:
        account_value = st.number_input(
            "Portfolio Waarde ($)",
            value=float(total_portfolio_value) if total_portfolio_value > 0 else 10000.0,
            min_value=100.0,
            step=100.0,
            help="Je totale beschikbare kapitaal"
        )
    with calc_cols[1]:
        risk_per_trade = st.slider(
            "Risico per Trade (%)",
            min_value=0.5,
            max_value=5.0,
            value=2.0,
            step=0.5,
            help="Hoeveel % van je portfolio wil je riskeren per trade? (1-2% aanbevolen)"
        )
    
    st.divider()
    
    st.subheader("📊 Trade Details")
    
    trade_cols = st.columns(3)
    with trade_cols[0]:
        calc_ticker = st.selectbox(
            "Stock",
            options=all_tickers + ["Custom"],
            key="calc_ticker"
        )
    
    if calc_ticker != "Custom":
        current_calc_price = latest(calc_ticker, "Close") if calc_ticker else 100
        sr = calculate_support_resistance(ticker_data.get(calc_ticker, pd.DataFrame()))
        suggested_stop = sr.get("support1", current_calc_price * 0.95) if sr else current_calc_price * 0.95
    else:
        current_calc_price = 100.0
        suggested_stop = 95.0
    
    with trade_cols[1]:
        entry_price = st.number_input(
            "Entry Prijs ($)",
            value=float(current_calc_price),
            min_value=0.01,
            step=0.01
        )
    with trade_cols[2]:
        stop_loss = st.number_input(
            "Stop Loss ($)",
            value=float(suggested_stop),
            min_value=0.01,
            step=0.01,
            help="Support level of maximum verlies"
        )
    
    # Calculate position size
    if st.button("🧮 Bereken Positiegrootte", use_container_width=True):
        result = calculate_position_size(account_value, risk_per_trade, entry_price, stop_loss)
        
        if result:
            st.divider()
            st.subheader("📊 Resultaat")
            
            # Risk-Based Method
            st.markdown("### 🎯 Risk-Based Position Size")
            
            res_cols = st.columns(4)
            res_cols[0].metric("Aantal Shares", f"{result['shares']}")
            res_cols[1].metric("Positie Waarde", f"${result['position_value']:,.2f}")
            res_cols[2].metric("% van Portfolio", f"{result['position_pct']:.1f}%")
            res_cols[3].metric("Risico ($)", f"${result['risk_amount']:,.2f}")
            
            st.info(f"""
            **Berekening:**
            - Risico bedrag: ${result['risk_amount']:,.2f} ({risk_per_trade}% van ${account_value:,.0f})
            - Risico per share: ${result['risk_per_share']:.2f} (Entry ${entry_price:.2f} - Stop ${stop_loss:.2f})
            - Aantal shares: ${result['risk_amount']:,.2f} / ${result['risk_per_share']:.2f} = **{result['shares']} shares**
            """)
            
            # Kelly Criterion
            st.markdown("### 📈 Kelly Criterion (Agressiever)")
            st.caption("Gebaseerd op 50% win rate en 2:1 reward/risk ratio")
            
            kelly_cols = st.columns(3)
            kelly_cols[0].metric("Kelly Shares", f"{result['kelly_shares']}")
            kelly_cols[1].metric("Kelly Waarde", f"${result['kelly_value']:,.2f}")
            kelly_cols[2].metric("Kelly %", f"{(result['kelly_value']/account_value*100):.1f}%")
            
            st.warning("""
            ⚠️ **Disclaimer:**
            - Risk-based sizing is conservatiever en veiliger
            - Kelly Criterion kan leiden tot grote posities
            - Gebruik maximaal 50% van Kelly ("Half Kelly") voor minder risico
            - Always DYOR en pas je risico aan op je comfort level
            """)
        else:
            st.error("Kon positiegrootte niet berekenen. Controleer je input.")


# ════════════════════════════════════════════════════════════════════════════
# TAB 12 – YouTuber Stock Picks (met automatische video analyse)
# ════════════════════════════════════════════════════════════════════════════
with tab12:
    st.header("🎬 YouTuber Stock Picks")
    st.info("""
    🎬 **Wat zie je hier?**  
    **Automatische video analyse!** Plak een YouTube video URL en we detecteren automatisch welke stocks 
    worden besproken door het transcript te analyseren. Je ziet ook het sentiment (bullish/bearish) per stock.
    Track de performance van YouTuber picks en zie wie het beste track record heeft!
    """)
    
    # Load data
    youtubers = user_data.get("youtubers", [])
    youtuber_picks = user_data.get("youtuber_picks", [])
    analyzed_videos = user_data.get("analyzed_videos", [])
    
    # ════════════════════════════════════════════════════════════════════════════
    # AUTOMATIC VIDEO ANALYSIS
    # ════════════════════════════════════════════════════════════════════════════
    st.subheader("🔍 Automatische Video Analyse")
    st.caption("Plak een YouTube video URL en we detecteren automatisch de besproken stocks!")
    
    analysis_cols = st.columns([2, 1.5, 1])
    with analysis_cols[0]:
        video_url = st.text_input(
            "YouTube Video URL",
            placeholder="https://www.youtube.com/watch?v=...",
            key="yt_video_url"
        )
    with analysis_cols[1]:
        video_youtuber = st.text_input(
            "YouTuber Naam",
            placeholder="Meet Kevin / Graham Stephan / etc.",
            key="video_youtuber"
        )
    with analysis_cols[2]:
        st.write("")
        st.write("")
        analyze_button = st.button("🔍 Analyseer Video", key="analyze_video", use_container_width=True)
    
    # Session state for detected stocks
    if "detected_stocks" not in st.session_state:
        st.session_state.detected_stocks = []
    if "analysis_status" not in st.session_state:
        st.session_state.analysis_status = ""
    
    if analyze_button:
        if video_url and video_youtuber:
            video_id = extract_video_id(video_url)
            if video_id:
                with st.spinner("📝 Transcript ophalen en analyseren..."):
                    transcript, error = get_youtube_transcript(video_id)
                    
                    if transcript:
                        # Detect stocks
                        detected = detect_stocks_in_text(transcript)
                        st.session_state.detected_stocks = detected
                        st.session_state.analysis_youtuber = video_youtuber
                        st.session_state.analysis_video_id = video_id
                        
                        if detected:
                            st.session_state.analysis_status = "success"
                            # Save analyzed video
                            analyzed_videos.append({
                                "video_id": video_id,
                                "url": video_url,
                                "youtuber": video_youtuber,
                                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "stocks_found": len(detected),
                            })
                            user_data["analyzed_videos"] = analyzed_videos
                            save_user_portfolio(st.session_state.current_user, user_data)
                        else:
                            st.session_state.analysis_status = "no_stocks"
                    else:
                        st.session_state.analysis_status = f"error: {error}"
                        st.session_state.detected_stocks = []
            else:
                st.error("❌ Ongeldige YouTube URL. Plak een volledige YouTube link.")
        else:
            st.warning("⚠️ Vul zowel de video URL als de YouTuber naam in.")
    
    # Show detection results
    if st.session_state.analysis_status == "success" and st.session_state.detected_stocks:
        st.success(f"✅ **{len(st.session_state.detected_stocks)} stocks gedetecteerd** in de video van {st.session_state.get('analysis_youtuber', 'Unknown')}!")
        
        st.markdown("### 📊 Gedetecteerde Stocks")
        st.caption("Sentiment is gebaseerd op gewogen keyword-analyse van de context rond elke stock mention.")
        
        # Display detected stocks with option to add
        for i, stock in enumerate(st.session_state.detected_stocks):
            det_cols = st.columns([1, 0.8, 1.2, 1.8, 0.6, 0.6])
            
            det_cols[0].markdown(f"**{stock['ticker']}**")
            det_cols[1].markdown(f"${stock['price']:.2f}" if stock['price'] > 0 else "N/A")
            
            # Show sentiment with score breakdown
            bull_score = stock.get('bullish_score', 0)
            bear_score = stock.get('bearish_score', 0)
            sentiment_text = stock['sentiment']
            if bull_score > 0 or bear_score > 0:
                det_cols[2].markdown(f"{sentiment_text}  \n`📈{bull_score} 📉{bear_score}`")
            else:
                det_cols[2].markdown(f"{sentiment_text}")
            
            # Show context snippet
            if stock.get('context'):
                det_cols[3].caption(f"_{stock['context'][:60]}_")
            else:
                det_cols[3].markdown("")
            
            # Add to picks button
            if det_cols[4].button("📊", key=f"add_detected_{i}_{stock['ticker']}", help="Track deze pick"):
                # Add to youtuber_picks
                youtuber_picks.append({
                    "youtuber": st.session_state.get('analysis_youtuber', 'Unknown'),
                    "ticker": stock['ticker'],
                    "sentiment": stock['sentiment'],
                    "target": None,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "price_at_pick": stock['price'],
                    "source": "auto_detected",
                    "video_id": st.session_state.get('analysis_video_id', ''),
                })
                user_data["youtuber_picks"] = youtuber_picks
                save_user_portfolio(st.session_state.current_user, user_data)
                st.success(f"✅ {stock['ticker']} tracking toegevoegd!")
                st.rerun()
            
            # Add to watchlist button (individual)
            if det_cols[5].button("👁️", key=f"add_wl_{i}_{stock['ticker']}", help="Toevoegen aan watchlist"):
                current_watchlist = user_data.get("watchlist_raw", "")
                current_tickers = [t.strip().upper() for t in current_watchlist.replace(",", " ").split() if t.strip()]
                
                if stock['ticker'] not in current_tickers:
                    current_tickers.append(stock['ticker'])
                    user_data["watchlist_raw"] = ", ".join(current_tickers)
                    save_user_portfolio(st.session_state.current_user, user_data)
                    st.success(f"✅ {stock['ticker']} aan watchlist toegevoegd!")
                else:
                    st.info(f"ℹ️ {stock['ticker']} staat al in je watchlist")
                st.rerun()
        
        st.caption("📊 = Track pick | 👁️ = Naar watchlist")
        
        # Bulk add button
        st.divider()
        
        # Sentiment override section
        with st.expander("✏️ Sentiment Aanpassen (optioneel)"):
            st.caption("Als je weet dat de YouTuber positiever/negatiever is over een stock dan gedetecteerd, pas het hier aan:")
            
            override_cols = st.columns([1, 2, 1])
            with override_cols[0]:
                override_ticker = st.selectbox(
                    "Stock",
                    options=[s['ticker'] for s in st.session_state.detected_stocks],
                    key="override_ticker"
                )
            with override_cols[1]:
                new_sentiment = st.radio(
                    "Nieuw Sentiment",
                    options=["🟢 Bullish", "🟡 Neutral", "🔴 Bearish"],
                    horizontal=True,
                    key="new_sentiment"
                )
            with override_cols[2]:
                st.write("")
                if st.button("✅ Update", key="apply_override"):
                    for stock in st.session_state.detected_stocks:
                        if stock['ticker'] == override_ticker:
                            stock['sentiment'] = new_sentiment
                    st.success(f"✅ {override_ticker} sentiment geüpdatet naar {new_sentiment}")
                    st.rerun()
        
        if st.button("📥 Voeg ALLE gedetecteerde stocks toe", key="add_all_detected", type="primary"):
            added_count = 0
            for stock in st.session_state.detected_stocks:
                # Check if not already tracked from this video
                already_exists = any(
                    p["ticker"] == stock["ticker"] and 
                    p.get("video_id") == st.session_state.get('analysis_video_id', '')
                    for p in youtuber_picks
                )
                if not already_exists:
                    youtuber_picks.append({
                        "youtuber": st.session_state.get('analysis_youtuber', 'Unknown'),
                        "ticker": stock['ticker'],
                        "sentiment": stock['sentiment'],
                        "target": None,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "price_at_pick": stock['price'],
                        "source": "auto_detected",
                        "video_id": st.session_state.get('analysis_video_id', ''),
                    })
                    added_count += 1
            
            user_data["youtuber_picks"] = youtuber_picks
            save_user_portfolio(st.session_state.current_user, user_data)
            st.success(f"✅ {added_count} stocks toegevoegd aan tracking!")
            st.session_state.detected_stocks = []
            st.session_state.analysis_status = ""
            st.rerun()
        
        # Add to watchlist button
        add_wl_cols = st.columns(2)
        with add_wl_cols[0]:
            if st.button("👁️ Voeg ALLE toe aan Watchlist", key="add_all_to_watchlist"):
                # Get current watchlist
                current_watchlist = user_data.get("watchlist_raw", "")
                current_tickers = [t.strip().upper() for t in current_watchlist.replace(",", " ").split() if t.strip()]
                
                added_to_wl = 0
                for stock in st.session_state.detected_stocks:
                    if stock['ticker'] not in current_tickers:
                        current_tickers.append(stock['ticker'])
                        added_to_wl += 1
                
                # Save updated watchlist
                user_data["watchlist_raw"] = ", ".join(current_tickers)
                save_user_portfolio(st.session_state.current_user, user_data)
                st.success(f"✅ {added_to_wl} stocks toegevoegd aan watchlist!")
                st.rerun()
        
        with add_wl_cols[1]:
            if st.button("📥👁️ Voeg ALLE toe aan BEIDE (Track + Watchlist)", key="add_all_both", type="secondary"):
                # Add to YouTuber picks
                added_picks = 0
                for stock in st.session_state.detected_stocks:
                    already_exists = any(
                        p["ticker"] == stock["ticker"] and 
                        p.get("video_id") == st.session_state.get('analysis_video_id', '')
                        for p in youtuber_picks
                    )
                    if not already_exists:
                        youtuber_picks.append({
                            "youtuber": st.session_state.get('analysis_youtuber', 'Unknown'),
                            "ticker": stock['ticker'],
                            "sentiment": stock['sentiment'],
                            "target": None,
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "price_at_pick": stock['price'],
                            "source": "auto_detected",
                            "video_id": st.session_state.get('analysis_video_id', ''),
                        })
                        added_picks += 1
                
                # Add to watchlist
                current_watchlist = user_data.get("watchlist_raw", "")
                current_tickers = [t.strip().upper() for t in current_watchlist.replace(",", " ").split() if t.strip()]
                
                added_wl = 0
                for stock in st.session_state.detected_stocks:
                    if stock['ticker'] not in current_tickers:
                        current_tickers.append(stock['ticker'])
                        added_wl += 1
                
                # Save both
                user_data["youtuber_picks"] = youtuber_picks
                user_data["watchlist_raw"] = ", ".join(current_tickers)
                save_user_portfolio(st.session_state.current_user, user_data)
                
                st.success(f"✅ {added_picks} stocks naar tracking + {added_wl} naar watchlist!")
                st.session_state.detected_stocks = []
                st.session_state.analysis_status = ""
                st.rerun()
    
    elif st.session_state.analysis_status == "no_stocks":
        st.warning("⚠️ Geen stocks gedetecteerd in deze video. Mogelijk bevat de video geen specifieke stock aanbevelingen.")
    
    elif st.session_state.analysis_status.startswith("error:"):
        st.error(f"❌ {st.session_state.analysis_status.replace('error: ', '')}")
    
    st.divider()
    
    # ════════════════════════════════════════════════════════════════════════════
    # MANUAL PICK LOGGING (optional)
    # ════════════════════════════════════════════════════════════════════════════
    with st.expander("📝 Handmatig Stock Pick Loggen (optioneel)"):
        st.caption("Voor als je een pick wilt loggen zonder video analyse.")
        
        manual_cols = st.columns([1.5, 1, 1, 1, 1, 1])
        
        with manual_cols[0]:
            manual_youtuber = st.text_input("YouTuber", placeholder="Meet Kevin", key="manual_youtuber")
        with manual_cols[1]:
            manual_ticker = st.text_input("Ticker", placeholder="AAPL", key="manual_ticker").upper()
        with manual_cols[2]:
            manual_sentiment = st.selectbox(
                "Sentiment",
                options=["🟢 Bullish", "🟡 Neutral", "🔴 Bearish"],
                key="manual_sentiment"
            )
        with manual_cols[3]:
            manual_target = st.number_input("Target ($)", value=0.0, min_value=0.0, step=1.0, key="manual_target")
        with manual_cols[4]:
            manual_date = st.date_input("Datum", value=datetime.now(), key="manual_date")
        with manual_cols[5]:
            st.write("")
            st.write("")
            if st.button("📝 Log Pick", key="log_manual_pick", use_container_width=True):
                if manual_ticker.strip() and manual_youtuber.strip():
                    current_price = get_current_price(manual_ticker)
                    youtuber_picks.append({
                        "youtuber": manual_youtuber.strip(),
                        "ticker": manual_ticker.strip(),
                        "sentiment": manual_sentiment,
                        "target": manual_target if manual_target > 0 else None,
                        "date": manual_date.strftime("%Y-%m-%d"),
                        "price_at_pick": current_price,
                        "source": "manual",
                    })
                    user_data["youtuber_picks"] = youtuber_picks
                    save_user_portfolio(st.session_state.current_user, user_data)
                    st.success(f"✅ {manual_ticker} pick van {manual_youtuber} gelogd!")
                    st.rerun()
                else:
                    st.error("Vul zowel YouTuber als Ticker in")
    
    st.divider()
    
    # ════════════════════════════════════════════════════════════════════════════
    # ANALYZED VIDEOS HISTORY
    # ════════════════════════════════════════════════════════════════════════════
    if analyzed_videos:
        with st.expander(f"📺 Geanalyseerde Video's ({len(analyzed_videos)})"):
            for vid in sorted(analyzed_videos, key=lambda x: x["date"], reverse=True)[:10]:
                vid_cols = st.columns([2, 1.5, 1, 0.5])
                vid_cols[0].markdown(f"**{vid['youtuber']}**")
                vid_cols[1].markdown(f"📅 {vid['date']}")
                vid_cols[2].markdown(f"🔢 {vid['stocks_found']} stocks")
                vid_cols[3].markdown(f"[🔗](https://youtube.com/watch?v={vid['video_id']})")
    
    # ════════════════════════════════════════════════════════════════════════════
    # PICKS OVERVIEW & PERFORMANCE
    # ════════════════════════════════════════════════════════════════════════════
    if youtuber_picks:
        st.subheader("📊 Alle Picks & Performance")
        
        # Calculate performance for each pick
        picks_with_perf = []
        for pick in youtuber_picks:
            ticker = pick["ticker"]
            price_at_pick = pick.get("price_at_pick", 0)
            
            # Get current price
            df = ticker_data.get(ticker, pd.DataFrame())
            if not df.empty:
                current_price = df["Close"].iloc[-1]
            else:
                current_price = get_current_price(ticker)
            
            # Calculate performance
            if price_at_pick > 0:
                performance = ((current_price - price_at_pick) / price_at_pick) * 100
            else:
                performance = 0
            
            picks_with_perf.append({
                **pick,
                "current_price": current_price,
                "performance": performance,
            })
        
        # Sort by date (newest first)
        picks_with_perf = sorted(picks_with_perf, key=lambda x: x["date"], reverse=True)
        
        # Display picks
        for idx, pick in enumerate(picks_with_perf):
            perf = pick["performance"]
            perf_color = "#3fb950" if perf >= 0 else "#f85149"
            sentiment_emoji = pick["sentiment"].split()[0]
            source_badge = "🤖" if pick.get("source") == "auto_detected" else "✍️"
            
            col1, col2, col3, col4, col5, col6 = st.columns([1.5, 1.2, 1.2, 1, 1.2, 0.5])
            col1.markdown(f"**{pick['youtuber']}** {source_badge}")
            col2.markdown(f"**{pick['ticker']}** {sentiment_emoji}")
            col3.markdown(f"${pick.get('price_at_pick', 0):.2f} → ${pick['current_price']:.2f}")
            col4.markdown(f"<span style='color:{perf_color}; font-weight:700'>{perf:+.1f}%</span>", unsafe_allow_html=True)
            col5.markdown(f"📅 {pick['date']}")
            if col6.button("🗑️", key=f"del_pick_{idx}_{pick['date']}_{pick['ticker']}"):
                youtuber_picks = [p for i, p in enumerate(youtuber_picks) if i != idx]
                user_data["youtuber_picks"] = youtuber_picks
                save_user_portfolio(st.session_state.current_user, user_data)
                st.rerun()
        
        st.caption("🤖 = Auto-detected | ✍️ = Handmatig toegevoegd")
        
        st.divider()
        
        # ════════════════════════════════════════════════════════════════════════════
        # LEADERBOARD
        # ════════════════════════════════════════════════════════════════════════════
        st.subheader("🏆 YouTuber Leaderboard")
        
        # Calculate stats per YouTuber
        yt_stats = {}
        for pick in picks_with_perf:
            yt = pick["youtuber"]
            if yt not in yt_stats:
                yt_stats[yt] = {"picks": 0, "total_perf": 0, "wins": 0}
            yt_stats[yt]["picks"] += 1
            yt_stats[yt]["total_perf"] += pick["performance"]
            if pick["performance"] > 0:
                yt_stats[yt]["wins"] += 1
        
        # Sort by average performance
        leaderboard = []
        for yt, stats in yt_stats.items():
            avg_perf = stats["total_perf"] / stats["picks"] if stats["picks"] > 0 else 0
            win_rate = (stats["wins"] / stats["picks"] * 100) if stats["picks"] > 0 else 0
            leaderboard.append({
                "youtuber": yt,
                "picks": stats["picks"],
                "avg_perf": avg_perf,
                "win_rate": win_rate,
            })
        
        leaderboard = sorted(leaderboard, key=lambda x: x["avg_perf"], reverse=True)
        
        for i, yt in enumerate(leaderboard, 1):
            medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
            perf_color = "#3fb950" if yt["avg_perf"] >= 0 else "#f85149"
            
            lb_cols = st.columns([0.5, 2, 1, 1.5, 1.5])
            lb_cols[0].markdown(f"**{medal}**")
            lb_cols[1].markdown(f"**{yt['youtuber']}**")
            lb_cols[2].markdown(f"{yt['picks']} picks")
            lb_cols[3].markdown(f"<span style='color:{perf_color}; font-weight:700'>Avg: {yt['avg_perf']:+.1f}%</span>", unsafe_allow_html=True)
            lb_cols[4].markdown(f"Win rate: {yt['win_rate']:.0f}%")
        
        st.divider()
        
        # ════════════════════════════════════════════════════════════════════════════
        # OVERLAP DETECTOR
        # ════════════════════════════════════════════════════════════════════════════
        st.subheader("🔥 Stock Overlap (Meerdere YouTubers)")
        st.caption("Stocks die door meerdere YouTubers worden genoemd - mogelijke sterke signalen!")
        
        # Count how many YouTubers mention each stock
        stock_mentions = {}
        for pick in youtuber_picks:
            ticker = pick["ticker"]
            yt = pick["youtuber"]
            if ticker not in stock_mentions:
                stock_mentions[ticker] = set()
            stock_mentions[ticker].add(yt)
        
        # Filter stocks mentioned by multiple YouTubers
        overlaps = [(ticker, list(yters)) for ticker, yters in stock_mentions.items() if len(yters) >= 2]
        overlaps = sorted(overlaps, key=lambda x: len(x[1]), reverse=True)
        
        if overlaps:
            for ticker, yters in overlaps:
                df = ticker_data.get(ticker, pd.DataFrame())
                current_price = df["Close"].iloc[-1] if not df.empty else get_current_price(ticker)
                
                st.markdown(f"""
                🎯 **{ticker}** (${current_price:.2f}) - Genoemd door **{len(yters)}** YouTubers:  
                {', '.join(yters)}
                """)
        else:
            st.info("Nog geen stocks die door meerdere YouTubers genoemd worden. Analyseer meer video's!")
    
    else:
        st.info("🎬 Nog geen picks gelogd. Analyseer een YouTube video hierboven om te beginnen!")


# ════════════════════════════════════════════════════════════════════════════
# INTERACTIVE CHART (below both tabs)
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📊 Interactieve Koersgrafiek (Enhanced)")

chart_col, ctrl_col = st.columns([4, 1])
with ctrl_col:
    selected_ticker = st.selectbox(
        "Kies aandeel",
        options=all_tickers if all_tickers else ["–"],
        index=0,
    )
    period_choice = st.selectbox(
        "Periode",
        options=["3mo", "6mo", "1y", "2y", "5y"],
        index=2,
        format_func=lambda x: {
            "3mo": "3 maanden", "6mo": "6 maanden",
            "1y": "1 jaar", "2y": "2 jaar", "5y": "5 jaar"
        }[x],
    )
    
    st.divider()
    st.caption("📈 Indicatoren")
    show_bb = st.checkbox("Bollinger Bands", value=True)
    show_macd = st.checkbox("MACD", value=True)

with chart_col:
    if selected_ticker and selected_ticker != "–":
        chart_df = fetch_data(selected_ticker, period=period_choice)
        if not chart_df.empty:
            fig = build_enhanced_chart(chart_df, selected_ticker, show_bb=show_bb, show_macd=show_macd)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(f"Geen data beschikbaar voor **{selected_ticker}**.")


# ════════════════════════════════════════════════════════════════════════════
# TAB 13 – Earnings Calendar
# ════════════════════════════════════════════════════════════════════════════
with tab13:
    st.header("📅 Earnings Calendar")
    st.info("""
    📅 **Wat zie je hier?**  
    Overzicht van aankomende earnings reports voor jouw stocks. 
    Zie wanneer bedrijven rapporteren, hoeveel dagen tot earnings, en historische earnings surprises.
    """)
    
    @st.cache_data(ttl=3600)  # Cache for 1 hour
    def get_earnings_data(ticker: str) -> dict:
        """Fetch earnings data for a ticker using multiple methods."""
        try:
            stock = yf.Ticker(ticker)
            
            # Method 1: Try earnings_dates (most reliable for history)
            earnings_dates_df = None
            try:
                earnings_dates_df = stock.earnings_dates
            except:
                pass
            
            # Method 2: Try calendar
            calendar = None
            try:
                calendar = stock.calendar
            except:
                pass
            
            # Method 3: Try quarterly_earnings
            quarterly_earnings = None
            try:
                quarterly_earnings = stock.quarterly_earnings
            except:
                pass
            
            # Get next earnings date
            next_earnings = None
            earnings_time = "N/A"
            
            # Try to get next earnings from earnings_dates (future dates)
            if earnings_dates_df is not None and not earnings_dates_df.empty:
                try:
                    now = pd.Timestamp.now(tz='UTC') if earnings_dates_df.index.tz else pd.Timestamp.now()
                    future_dates = earnings_dates_df[earnings_dates_df.index > now]
                    if not future_dates.empty:
                        next_earnings = future_dates.index[0]
                except Exception:
                    # Fallback: just get the most recent index
                    try:
                        next_earnings = earnings_dates_df.index[0]
                    except:
                        pass
            
            # Fallback to calendar if no earnings_dates
            if next_earnings is None and calendar is not None:
                try:
                    if isinstance(calendar, pd.DataFrame) and not calendar.empty:
                        # Calendar can be DataFrame with index containing 'Earnings Date'
                        if 'Earnings Date' in calendar.index:
                            val = calendar.loc['Earnings Date'].iloc[0]
                            next_earnings = pd.to_datetime(val)
                        elif 'Earnings Date' in calendar.columns:
                            next_earnings = pd.to_datetime(calendar['Earnings Date'].iloc[0])
                    elif isinstance(calendar, dict):
                        if 'Earnings Date' in calendar:
                            dates = calendar['Earnings Date']
                            if dates:
                                next_earnings = pd.to_datetime(dates[0] if isinstance(dates, list) else dates)
                except Exception:
                    pass
            
            # Get historical earnings surprises
            surprises = []
            
            # Method A: From earnings_dates
            if earnings_dates_df is not None and not earnings_dates_df.empty:
                cols = earnings_dates_df.columns.tolist()
                
                for idx, row in earnings_dates_df.head(12).iterrows():
                    try:
                        # Handle timezone-aware datetime
                        if hasattr(idx, 'tz') and idx.tz is not None:
                            idx_naive = idx.tz_localize(None)
                        else:
                            idx_naive = idx
                        
                        # Try different column name formats
                        surprise_pct = None
                        reported_eps = None
                        estimated_eps = None
                        
                        for col in cols:
                            col_lower = col.lower()
                            if 'surprise' in col_lower and '%' in col_lower:
                                surprise_pct = row[col]
                            elif 'surprise' in col_lower:
                                surprise_pct = row[col]
                            elif 'reported' in col_lower or 'actual' in col_lower:
                                reported_eps = row[col]
                            elif 'estimate' in col_lower:
                                estimated_eps = row[col]
                        
                        # Check if values are valid
                        if pd.notna(reported_eps) or pd.notna(surprise_pct):
                            surprises.append({
                                "date": idx_naive.strftime('%Y-%m-%d') if hasattr(idx_naive, 'strftime') else str(idx)[:10],
                                "reported_eps": float(reported_eps) if pd.notna(reported_eps) else None,
                                "estimated_eps": float(estimated_eps) if pd.notna(estimated_eps) else None,
                                "surprise_pct": float(surprise_pct) if pd.notna(surprise_pct) else None,
                            })
                    except Exception:
                        continue
            
            # Method B: From quarterly_earnings if no surprises found
            if not surprises and quarterly_earnings is not None and not quarterly_earnings.empty:
                try:
                    for idx, row in quarterly_earnings.head(12).iterrows():
                        reported = row.get('Earnings', None) or row.get('Actual', None)
                        if pd.notna(reported):
                            surprises.append({
                                "date": str(idx)[:10] if idx else "N/A",
                                "reported_eps": float(reported),
                                "estimated_eps": None,
                                "surprise_pct": None,
                            })
                except Exception:
                    pass
            
            return {
                "next_earnings": next_earnings,
                "earnings_time": earnings_time,
                "surprises": surprises,
            }
        except Exception as e:
            return {"next_earnings": None, "earnings_time": "N/A", "surprises": [], "error": str(e)}
    
    if not all_tickers:
        st.info("Voeg stocks toe aan je portfolio of watchlist om earnings te zien.")
    else:
        # Fetch earnings data for all tickers
        earnings_data = {}
        upcoming_earnings = []
        
        with st.spinner("📅 Earnings data ophalen..."):
            for t in all_tickers:
                data = get_earnings_data(t)
                earnings_data[t] = data
                
                # Check if earnings date is available and upcoming
                if data.get("next_earnings") is not None:
                    try:
                        earnings_date = pd.to_datetime(data["next_earnings"])
                        # Remove timezone info for comparison
                        if hasattr(earnings_date, 'tz') and earnings_date.tz is not None:
                            earnings_date = earnings_date.tz_localize(None)
                        
                        now = pd.Timestamp.now()
                        days_until = (earnings_date - now).days
                        
                        if days_until >= -1:  # Include today and future
                            upcoming_earnings.append({
                                "ticker": t,
                                "date": earnings_date,
                                "days_until": days_until,
                            })
                    except Exception:
                        pass
        
        # Sort by days until earnings
        upcoming_earnings = sorted(upcoming_earnings, key=lambda x: x["days_until"])
        
        # ── Summary ──
        st.subheader("📊 Earnings Overview")
        
        col1, col2, col3, col4 = st.columns(4)
        
        this_week = sum(1 for e in upcoming_earnings if 0 <= e["days_until"] <= 7)
        next_week = sum(1 for e in upcoming_earnings if 7 < e["days_until"] <= 14)
        this_month = sum(1 for e in upcoming_earnings if 0 <= e["days_until"] <= 30)
        
        col1.metric("📅 This Week", this_week, help="Earnings in de komende 7 dagen")
        col2.metric("📆 Next Week", next_week, help="Earnings over 7-14 dagen")
        col3.metric("🗓️ This Month", this_month, help="Earnings in de komende 30 dagen")
        col4.metric("📋 Total Tracked", len(all_tickers))
        
        st.divider()
        
        # ── Upcoming Earnings Timeline ──
        st.subheader("⏰ Upcoming Earnings")
        
        if upcoming_earnings:
            for e in upcoming_earnings[:15]:  # Show top 15
                days = e["days_until"]
                
                # Determine urgency color
                if days <= 2:
                    bg_color = "#3d1515" if st.session_state.theme == "dark" else "#ffe0e0"
                    border_color = "#f85149"
                    urgency = "🔴 IMMINENT"
                elif days <= 7:
                    bg_color = "#3d3010" if st.session_state.theme == "dark" else "#fff3cd"
                    border_color = "#e3b341"
                    urgency = "🟡 This Week"
                elif days <= 14:
                    bg_color = "#1a2332" if st.session_state.theme == "dark" else "#cce5ff"
                    border_color = "#58a6ff"
                    urgency = "🔵 Next Week"
                else:
                    bg_color = "#1c1f26" if st.session_state.theme == "dark" else "#f0f0f0"
                    border_color = "#2e3140" if st.session_state.theme == "dark" else "#ccc"
                    urgency = "⚪ Upcoming"
                
                date_str = e["date"].strftime('%a, %d %b %Y') if hasattr(e["date"], 'strftime') else str(e["date"])
                
                st.markdown(f"""
                <div style="
                    background:{bg_color};
                    border:2px solid {border_color};
                    border-radius:12px;
                    padding:15px 20px;
                    margin-bottom:10px;
                    display:flex;
                    flex-wrap:wrap;
                    justify-content:space-between;
                    align-items:center;
                    gap:10px;
                ">
                    <div style="display:flex; align-items:center; gap:15px; flex-wrap:wrap">
                        <span style="font-size:1.3rem; font-weight:800; color:#e6edf3">{e['ticker']}</span>
                        <span style="padding:4px 12px; background:{border_color}; border-radius:20px; font-weight:700; color:#000; font-size:0.8rem">
                            {urgency}
                        </span>
                    </div>
                    <div style="text-align:right">
                        <div style="font-size:1.1rem; font-weight:700; color:#58a6ff">{date_str}</div>
                        <div style="font-size:0.9rem; color:#8b949e">
                            {'📢 TODAY!' if days == 0 else f'⏳ {days} dag{"en" if days != 1 else ""} te gaan'}
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Geen aankomende earnings gevonden voor je stocks.")
        
        st.divider()
        
        # ── All Stocks Earnings Status ──
        st.subheader("📋 Earnings Status per Stock")
        status_data = []
        for t in all_tickers:
            data = earnings_data.get(t, {})
            next_e = data.get("next_earnings")
            n_surprises = len(data.get("surprises", []))
            
            if next_e is not None:
                try:
                    ed = pd.to_datetime(next_e)
                    if hasattr(ed, 'tz') and ed.tz is not None:
                        ed = ed.tz_localize(None)
                    days = (ed - pd.Timestamp.now()).days
                    date_str = ed.strftime('%d %b %Y')
                    status = f"📅 {date_str} ({days}d)"
                except Exception:
                    status = f"📅 {str(next_e)[:10]}"
            else:
                status = "❓ Niet beschikbaar"
            
            status_data.append({"Ticker": t, "Next Earnings": status, "History": f"{n_surprises} kwartalen"})
        
        if status_data:
            st.dataframe(pd.DataFrame(status_data), use_container_width=True, hide_index=True)
        
        st.divider()
        
        # ── Historical Earnings Surprises ──
        st.subheader("📈 Historical Earnings Surprises")
        
        selected_ticker_earnings = st.selectbox(
            "Selecteer stock voor earnings history",
            options=all_tickers,
            key="earnings_history_select"
        )
        
        if selected_ticker_earnings:
            data = earnings_data.get(selected_ticker_earnings, {})
            surprises = data.get("surprises", [])
            
            if surprises:
                # Build chart
                dates = [s["date"] for s in surprises if s.get("surprise_pct") is not None]
                surprise_pcts = [s["surprise_pct"] for s in surprises if s.get("surprise_pct") is not None]
                
                if dates and surprise_pcts:
                    fig = go.Figure()
                    
                    colors = ["#3fb950" if s >= 0 else "#f85149" for s in surprise_pcts]
                    
                    fig.add_trace(go.Bar(
                        x=dates,
                        y=surprise_pcts,
                        marker_color=colors,
                        text=[f"{s:+.1f}%" for s in surprise_pcts],
                        textposition="outside",
                    ))
                    
                    fig.add_hline(y=0, line_dash="dash", line_color="#8b949e")
                    
                    fig.update_layout(
                        template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white",
                        height=350,
                        title=f"{selected_ticker_earnings} - Earnings Surprise History",
                        xaxis_title="Earnings Date",
                        yaxis_title="Surprise %",
                        showlegend=False
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                
                # Table view
                st.markdown("**📋 Details:**")
                for s in surprises:
                    if s.get("reported_eps") is not None:
                        surprise_color = "#3fb950" if (s.get("surprise_pct") or 0) >= 0 else "#f85149"
                        surprise_emoji = "✅" if (s.get("surprise_pct") or 0) >= 0 else "❌"
                        
                        est_str = f"Est: ${s['estimated_eps']:.2f}" if s.get("estimated_eps") else "Est: N/A"
                        rep_str = f"${s['reported_eps']:.2f}"
                        surp_str = f"{s['surprise_pct']:+.1f}%" if s.get("surprise_pct") else "N/A"
                        
                        st.markdown(f"""
                        <div style="display:flex; flex-wrap:wrap; gap:10px; padding:8px 0; border-bottom:1px solid #2e3140; align-items:center">
                            <span style="min-width:100px; color:#8b949e">{s['date']}</span>
                            <span style="min-width:80px">{est_str}</span>
                            <span style="min-width:80px; font-weight:700">→ {rep_str}</span>
                            <span style="font-weight:700; color:{surprise_color}">{surprise_emoji} {surp_str}</span>
                        </div>
                        """, unsafe_allow_html=True)
            else:
                st.info(f"Geen earnings history beschikbaar voor {selected_ticker_earnings}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 14 – News Feed
# ════════════════════════════════════════════════════════════════════════════
with tab14:
    st.header("📰 News Feed")
    st.info("""
    📰 **Wat zie je hier?**  
    Laatste nieuws voor jouw stocks met automatische sentiment analyse. 
    Blijf op de hoogte van belangrijke ontwikkelingen die je posities kunnen beïnvloeden.
    """)
    
    @st.cache_data(ttl=600)  # Cache for 10 minutes
    def get_stock_news(ticker: str) -> list:
        """Fetch news for a ticker - handles multiple yfinance API versions."""
        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            
            if not news:
                return []
            
            articles = []
            for item in news[:10]:
                # Handle both old and new yfinance API formats
                title = item.get("title", "")
                publisher = item.get("publisher", "")
                link = item.get("link", "#")
                published = item.get("providerPublishTime", 0)
                
                # New yfinance format wraps content differently
                if not title and "content" in item:
                    content = item.get("content", {})
                    title = content.get("title", item.get("title", "No title"))
                    publisher = content.get("provider", {}).get("displayName", "Unknown")
                    link = content.get("canonicalUrl", {}).get("url", item.get("link", "#"))
                    pub_date = content.get("pubDate", "")
                    if pub_date:
                        try:
                            published = int(pd.to_datetime(pub_date).timestamp())
                        except Exception:
                            published = 0
                
                if not title:
                    title = "No title"
                if not publisher:
                    publisher = "Unknown"
                
                # Handle thumbnail
                thumbnail = None
                try:
                    if "thumbnail" in item:
                        thumbnail = item["thumbnail"].get("resolutions", [{}])[0].get("url")
                    elif "content" in item:
                        thumb = item["content"].get("thumbnail", {})
                        if thumb and "resolutions" in thumb:
                            thumbnail = thumb["resolutions"][0].get("url")
                except Exception:
                    pass
                
                articles.append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "published": published,
                    "thumbnail": thumbnail,
                })
            
            return articles
        except Exception:
            return []
    
    def analyze_sentiment(text: str) -> tuple:
        """Simple sentiment analysis based on keywords."""
        text_lower = text.lower()
        
        # Positive keywords
        positive_words = [
            'surge', 'soar', 'jump', 'gain', 'rise', 'climb', 'rally', 'beat', 'exceed',
            'upgrade', 'bullish', 'growth', 'profit', 'success', 'record', 'breakthrough',
            'innovative', 'strong', 'positive', 'optimistic', 'outperform', 'buy', 'winner',
            'boom', 'skyrocket', 'best', 'high', 'up', 'boost', 'advantage'
        ]
        
        # Negative keywords
        negative_words = [
            'fall', 'drop', 'plunge', 'crash', 'decline', 'sink', 'tumble', 'miss', 'cut',
            'downgrade', 'bearish', 'loss', 'fail', 'weak', 'negative', 'pessimistic',
            'underperform', 'sell', 'loser', 'bust', 'worst', 'low', 'down', 'risk',
            'warning', 'concern', 'fear', 'lawsuit', 'investigation', 'recall'
        ]
        
        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)
        
        if pos_count > neg_count:
            score = min(100, 50 + (pos_count - neg_count) * 10)
            return ("🟢 Bullish", score, "#3fb950")
        elif neg_count > pos_count:
            score = max(0, 50 - (neg_count - pos_count) * 10)
            return ("🔴 Bearish", score, "#f85149")
        else:
            return ("🟡 Neutral", 50, "#e3b341")
    
    if not all_tickers:
        st.info("Voeg stocks toe aan je portfolio of watchlist om nieuws te zien.")
    else:
        # News source selector
        news_mode = st.radio(
            "Toon nieuws voor:",
            ["🔥 Alle Stocks", "📁 Portfolio Only", "👁️ Watchlist Only", "🔍 Specifieke Stock"],
            horizontal=True,
            key="news_mode"
        )
        
        # Determine which tickers to show news for
        if news_mode == "📁 Portfolio Only":
            news_tickers = [p["ticker"] for p in portfolio_positions]
        elif news_mode == "👁️ Watchlist Only":
            news_tickers = watchlist_tickers
        elif news_mode == "🔍 Specifieke Stock":
            selected_news_ticker = st.selectbox("Selecteer stock", all_tickers, key="news_ticker_select")
            news_tickers = [selected_news_ticker] if selected_news_ticker else []
        else:
            news_tickers = all_tickers[:10]  # Limit to 10 for performance
        
        st.divider()
        
        # ── Sentiment Summary ──
        if news_mode != "🔍 Specifieke Stock":
            st.subheader("📊 Sentiment Overview")
            
            all_news = []
            sentiment_summary = {"bullish": 0, "bearish": 0, "neutral": 0}
            
            with st.spinner("📰 Nieuws ophalen..."):
                for t in news_tickers:
                    articles = get_stock_news(t)
                    for article in articles:
                        sentiment, score, color = analyze_sentiment(article["title"])
                        article["ticker"] = t
                        article["sentiment"] = sentiment
                        article["sentiment_score"] = score
                        article["sentiment_color"] = color
                        all_news.append(article)
                        
                        if "Bullish" in sentiment:
                            sentiment_summary["bullish"] += 1
                        elif "Bearish" in sentiment:
                            sentiment_summary["bearish"] += 1
                        else:
                            sentiment_summary["neutral"] += 1
            
            # Sentiment metrics
            total_articles = len(all_news)
            if total_articles > 0:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("📰 Total Articles", total_articles)
                col2.metric("🟢 Bullish", sentiment_summary["bullish"], 
                           delta=f"{sentiment_summary['bullish']/total_articles*100:.0f}%")
                col3.metric("🔴 Bearish", sentiment_summary["bearish"],
                           delta=f"-{sentiment_summary['bearish']/total_articles*100:.0f}%" if sentiment_summary["bearish"] > 0 else None)
                col4.metric("🟡 Neutral", sentiment_summary["neutral"])
            
            st.divider()
        
        # ── News Articles ──
        st.subheader("📰 Latest News")
        
        # Sort by published time (most recent first)
        if news_mode == "🔍 Specifieke Stock":
            with st.spinner("📰 Nieuws ophalen..."):
                all_news = []
                for t in news_tickers:
                    articles = get_stock_news(t)
                    for article in articles:
                        sentiment, score, color = analyze_sentiment(article["title"])
                        article["ticker"] = t
                        article["sentiment"] = sentiment
                        article["sentiment_score"] = score
                        article["sentiment_color"] = color
                        all_news.append(article)
        
        all_news = sorted(all_news, key=lambda x: x.get("published", 0), reverse=True)
        
        if all_news:
            for article in all_news[:20]:  # Show max 20 articles
                published_time = datetime.fromtimestamp(article["published"]).strftime('%d %b %Y, %H:%M') if article["published"] else "Unknown"
                
                # Card color based on sentiment
                if "Bullish" in article["sentiment"]:
                    bg_color = "#0d2318"
                    border_color = "#3fb950"
                elif "Bearish" in article["sentiment"]:
                    bg_color = "#1a0d0d"
                    border_color = "#f85149"
                else:
                    bg_color = "#1c1f26"
                    border_color = "#2e3140"
                
                st.markdown(f"""
                <div style="
                    background:{bg_color};
                    border:2px solid {border_color};
                    border-radius:12px;
                    padding:15px;
                    margin-bottom:12px;
                ">
                    <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-start; gap:10px; margin-bottom:8px">
                        <div style="flex:1; min-width:200px">
                            <a href="{article['link']}" target="_blank" style="
                                font-size:1.05rem; 
                                font-weight:700; 
                                color:#e6edf3; 
                                text-decoration:none;
                                line-height:1.4;
                            ">{article['title']}</a>
                        </div>
                        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
                            <span style="padding:4px 10px; background:#1a2332; border-radius:15px; font-weight:700; color:#58a6ff; font-size:0.8rem">
                                {article['ticker']}
                            </span>
                            <span style="padding:4px 10px; background:{article['sentiment_color']}20; border:1px solid {article['sentiment_color']}; border-radius:15px; font-weight:600; color:{article['sentiment_color']}; font-size:0.75rem">
                                {article['sentiment']}
                            </span>
                        </div>
                    </div>
                    <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; font-size:0.8rem; color:#8b949e">
                        <span>📰 {article['publisher']}</span>
                        <span>🕐 {published_time}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Geen nieuws gevonden voor de geselecteerde stocks.")
        
        # ── Quick Links ──
        st.divider()
        st.subheader("🔗 Quick Links")
        
        link_cols = st.columns(4)
        for idx, t in enumerate(news_tickers[:4]):
            with link_cols[idx]:
                st.markdown(f"""
                <div style="text-align:center; padding:15px; background:#1c1f26; border-radius:10px; border:1px solid #2e3140">
                    <div style="font-weight:700; color:#e6edf3; margin-bottom:8px">{t}</div>
                    <a href="https://finance.yahoo.com/quote/{t}" target="_blank" style="color:#58a6ff; font-size:0.85rem">Yahoo Finance →</a><br>
                    <a href="https://www.google.com/search?q={t}+stock+news" target="_blank" style="color:#58a6ff; font-size:0.85rem">Google News →</a>
                </div>
                """, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 15 – Stock Screener
# ════════════════════════════════════════════════════════════════════════════
with tab15:
    st.header("🔍 Stock Screener")
    
    st.markdown("""
    Scan 60+ populaire stocks met filters op RSI, trend, fair value en fundamentals.
    Vind de beste kansen op basis van jouw criteria.
    """)
    
    # Screener filters
    st.subheader("⚙️ Filters")
    
    fcol1, fcol2, fcol3 = st.columns(3)
    
    with fcol1:
        rsi_filter = st.slider("RSI Range", 0, 100, (0, 70), key="scr_rsi")
        min_upside = st.number_input("Min Fair Value Upside (%)", value=0.0, step=5.0, key="scr_upside")
    
    with fcol2:
        trend_filter = st.multiselect(
            "Trend", 
            options=["🟢 Bullish", "🔴 Bearish"],
            default=["🟢 Bullish", "🔴 Bearish"],
            key="scr_trend"
        )
        signal_filter = st.multiselect(
            "Signal",
            options=["BUY", "HOLD", "SELL"],
            default=["BUY", "HOLD", "SELL"],
            key="scr_signal"
        )
    
    with fcol3:
        sector_filter = st.multiselect(
            "Sector (laat leeg = alle)",
            options=["Technology", "Financial Services", "Healthcare", "Consumer Cyclical", 
                     "Communication Services", "Consumer Defensive", "Energy", "Industrials"],
            key="scr_sector"
        )
        sort_by = st.selectbox(
            "Sorteer op",
            options=["Fair Value Upside", "RSI (laag → hoog)", "Daily Change", "Buy Score"],
            key="scr_sort"
        )
    
    # Run screener
    if st.button("🔍 Start Screener", use_container_width=True, type="primary", key="run_screener"):
        # Ensure popular stocks data is loaded
        screener_tickers = list(set(all_tickers + popular_tickers_list))
        
        screener_results = []
        progress = st.progress(0, text="Scanning stocks...")
        
        for i, t in enumerate(screener_tickers):
            progress.progress((i + 1) / len(screener_tickers), text=f"Scanning {t}...")
            
            # Fetch data if not cached
            if t not in ticker_data:
                ticker_data[t] = fetch_data(t)
            
            df = ticker_data.get(t, pd.DataFrame())
            if df.empty or len(df) < 30:
                continue
            
            price = float(df["Close"].iloc[-1])
            if price <= 0:
                continue
            
            # Calculate indicators
            rsi = float(df["RSI"].iloc[-1]) if "RSI" in df.columns and not pd.isna(df["RSI"].iloc[-1]) else 50
            sma200 = float(df["SMA200"].iloc[-1]) if "SMA200" in df.columns and not pd.isna(df["SMA200"].iloc[-1]) else price
            sma50 = float(df["SMA50"].iloc[-1]) if "SMA50" in df.columns and not pd.isna(df["SMA50"].iloc[-1]) else price
            daily_chg = float(df["Change_Pct"].iloc[-1]) if "Change_Pct" in df.columns and not pd.isna(df["Change_Pct"].iloc[-1]) else 0
            
            fair_val = calculate_fair_value(df, price, t)
            upside = fair_val["upside"]
            signal = classify_status(price, sma200, rsi, df, fair_val["fair_value"])
            
            trend = "🟢 Bullish" if price > sma200 else "🔴 Bearish"
            
            info = fetch_ticker_info(t)
            sector = info.get("sector", "Unknown")
            pe = info.get("pe_ratio")
            div_yield = info.get("dividend_yield", 0) or 0
            
            # Apply filters
            if rsi < rsi_filter[0] or rsi > rsi_filter[1]:
                continue
            if upside < min_upside:
                continue
            if trend_filter and trend not in trend_filter:
                continue
            if signal_filter and signal not in signal_filter:
                continue
            if sector_filter and sector not in sector_filter:
                continue
            
            # Calculate buy score
            buy_score = 50
            if rsi < 30: buy_score += 20
            elif rsi < 40: buy_score += 10
            elif rsi > 70: buy_score -= 15
            if upside > 20: buy_score += 20
            elif upside > 10: buy_score += 10
            elif upside < -10: buy_score -= 10
            if trend == "🟢 Bullish": buy_score += 5
            buy_score = max(0, min(100, buy_score))
            
            # 52w range position
            high_52w = float(df["High"].tail(252).max()) if len(df) >= 252 else float(df["High"].max())
            low_52w = float(df["Low"].tail(252).min()) if len(df) >= 252 else float(df["Low"].min())
            range_pos = ((price - low_52w) / (high_52w - low_52w) * 100) if (high_52w - low_52w) > 0 else 50
            
            screener_results.append({
                "Ticker": t,
                "Price": price,
                "Daily": daily_chg,
                "RSI": rsi,
                "Signal": signal,
                "Trend": trend,
                "Upside": upside,
                "FV": fair_val["fair_value"],
                "Sector": sector,
                "P/E": pe,
                "Div": div_yield,
                "Score": buy_score,
                "52W Pos": range_pos,
            })
        
        progress.empty()
        
        # Sort results
        if sort_by == "Fair Value Upside":
            screener_results.sort(key=lambda x: x["Upside"], reverse=True)
        elif sort_by == "RSI (laag → hoog)":
            screener_results.sort(key=lambda x: x["RSI"])
        elif sort_by == "Daily Change":
            screener_results.sort(key=lambda x: x["Daily"], reverse=True)
        elif sort_by == "Buy Score":
            screener_results.sort(key=lambda x: x["Score"], reverse=True)
        
        # Store in session state
        st.session_state.screener_results = screener_results
    
    # Display results
    if "screener_results" in st.session_state and st.session_state.screener_results:
        results = st.session_state.screener_results
        
        st.subheader(f"📊 {len(results)} Resultaten")
        
        # Summary
        scol1, scol2, scol3, scol4 = st.columns(4)
        buy_count = sum(1 for r in results if r["Signal"] == "BUY")
        avg_upside = sum(r["Upside"] for r in results) / len(results) if results else 0
        avg_rsi = sum(r["RSI"] for r in results) / len(results) if results else 50
        
        scol1.metric("🟢 Buy Signals", buy_count)
        scol2.metric("📈 Gem. Upside", f"{avg_upside:+.1f}%")
        scol3.metric("📊 Gem. RSI", f"{avg_rsi:.0f}")
        scol4.metric("🔍 Gevonden", len(results))
        
        st.divider()
        
        # Results cards
        for r in results[:25]:
            pnl_color = "#3fb950" if r["Upside"] >= 0 else "#f85149"
            daily_color = "#3fb950" if r["Daily"] >= 0 else "#f85149"
            signal_color = "#3fb950" if r["Signal"] == "BUY" else ("#f85149" if r["Signal"] == "SELL" else "#e3b341")
            score_color = "#3fb950" if r["Score"] >= 70 else ("#e3b341" if r["Score"] >= 50 else "#8b949e")
            rsi_color = "#3fb950" if r["RSI"] < 35 else ("#f85149" if r["RSI"] > 65 else "#e3b341")
            
            bg = "#141a24" if st.session_state.theme == "dark" else "#f8fafc"
            text_color = "#e6edf3" if st.session_state.theme == "dark" else "#000"
            sub_color = "#8b949e" if st.session_state.theme == "dark" else "#666"
            card_bg = "#0f1319" if st.session_state.theme == "dark" else "#f0f4f8"
            
            # In portfolio check
            in_portfolio = r["Ticker"] in [p["ticker"] for p in portfolio_positions]
            portfolio_badge = ' <span style="padding:2px 8px; background:#58a6ff; border-radius:10px; font-size:0.7rem; color:#000; font-weight:700">IN PORTFOLIO</span>' if in_portfolio else ""
            
            pe_str = f"{r['P/E']:.1f}" if r["P/E"] else "N/A"
            
            st.markdown(f"""
            <div style="background:{bg}; border:2px solid {'#3fb950' if r['Signal'] == 'BUY' else '#2e3140'}; border-radius:12px; padding:14px; margin-bottom:8px;">
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px">
                    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap">
                        <span style="font-size:1.3rem; font-weight:800; color:{text_color}">{r['Ticker']}</span>
                        <span style="padding:3px 10px; background:{signal_color}; border-radius:15px; font-weight:700; color:#000; font-size:0.75rem">{r['Signal']}</span>
                        <span style="padding:3px 10px; background:{score_color}; border-radius:15px; font-weight:700; color:#000; font-size:0.75rem">{r['Score']}/100</span>
                        {portfolio_badge}
                    </div>
                    <div style="text-align:right">
                        <span style="font-size:1.15rem; font-weight:700; color:{text_color}">${r['Price']:.2f}</span>
                        <span style="margin-left:8px; font-weight:700; color:{daily_color}">{r['Daily']:+.2f}%</span>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(75px, 1fr)); gap:6px;">
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">RSI</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{rsi_color}">{r['RSI']:.0f}</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">Fair Value</div>
                        <div style="font-size:0.85rem; font-weight:700; color:#58a6ff">${r['FV']:.2f}</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">Upside</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{pnl_color}">{r['Upside']:+.1f}%</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">P/E</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{text_color}">{pe_str}</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">Trend</div>
                        <div style="font-size:0.85rem; font-weight:700">{r['Trend']}</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">Sector</div>
                        <div style="font-size:0.7rem; font-weight:600; color:{text_color}">{r['Sector'][:12]}</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">52W Pos</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['52W Pos'] < 30 else '#f85149' if r['52W Pos'] > 70 else '#e3b341'}">{r['52W Pos']:.0f}%</div>
                    </div>
                    <div style="text-align:center; padding:5px; background:{card_bg}; border-radius:6px">
                        <div style="font-size:0.6rem; color:{sub_color}">Dividend</div>
                        <div style="font-size:0.85rem; font-weight:700; color:{'#3fb950' if r['Div'] > 0.02 else sub_color}">{r['Div']*100:.1f}%</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    elif "screener_results" not in st.session_state:
        st.info("Klik 'Start Screener' om te beginnen met scannen.")


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="text-align:center; color:#484f58; font-size:.78rem; margin-top:32px">
      Stock Dashboard · Data via Yahoo Finance · Refresh elke 5 min
    </div>
    """,
    unsafe_allow_html=True,
)
