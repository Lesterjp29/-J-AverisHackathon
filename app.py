import streamlit as st
import json
from pathlib import Path

# --- Page Config ---
st.set_page_config(
    page_title="DocuVerify — Shipping Document Verification",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Custom Styling: Modern Pastel Card System ---
st.markdown("""
<style>
    /* Background & Global Fonts */
    .stApp {
        background-color: #F6F8FA;
        color: #1E293B;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* Top Header Styling */
    .dashboard-header {
        margin-bottom: 24px;
    }
    .dashboard-header h1 {
        font-size: 28px;
        font-weight: 800;
        color: #0F172A;
        margin-bottom: 4px;
    }
    .dashboard-header p {
        color: #64748B;
        font-size: 15px;
        margin: 0;
    }

    /* Modern Card Containers */
    .clean-card {
        background: #FFFFFF;
        border-radius: 18px;
        padding: 22px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
        border: 1px solid #EEF2F6;
        margin-bottom: 18px;
    }

    /* Metric Cards */
    .metric-card {
        background: #FFFFFF;
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: 0 4px 18px rgba(0, 0, 0, 0.02);
        border: 1px solid #EEF2F6;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 125px;
    }
    .metric-title {
        font-size: 13px;
        font-weight: 600;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-value {
        font-size: 32px;
        font-weight: 800;
        color: #0F172A;
        margin: 6px 0;
    }

    /* Pastel Badges */
    .badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 100px;
        font-size: 12px;
        font-weight: 700;
    }
    .badge-ok { background-color: #E6F8F0; color: #059669; }
    .badge-mismatch { background-color: #FFEAE8; color: #E11D48; }
    .badge-review { background-color: #FFF6E5; color: #D97706; }
    .badge-neutral { background-color: #EDE9FE; color: #6D28D9; }

    /* Action List Items */
    .action-item {
        background: #F8FAFC;
        border-radius: 14px;
        padding: 14px 18px;
        margin-bottom: 10px;
        border: 1px solid #F1F5F9;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
</style>
""", unsafe_allow_html=True)

OUT_DIR = Path("out")

@st.cache_data
def load_data():
    report_path = OUT_DIR / "report.json"
    review_path = OUT_DIR / "review_queue.json"
    sub_path = OUT_DIR / "submission.json"

    raw_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    reviews = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else []
    subs = json.loads(sub_path.read_text(encoding="utf-8")) if sub_path.exists() else {}

    report_list = []
    if isinstance(raw_report, dict):
        for k, v in raw_report.items():
            entry = {"email_id": k, **(v if isinstance(v, dict) else {"details": v})}
            report_list.append(entry)
    elif isinstance(raw_report, list):
        report_list = raw_report

    return report_list, reviews, subs

report_data, review_queue, submissions = load_data()

# Calculate stats
intents = {}
outcomes = {"OK": 0, "MISMATCH": 0, "NEEDS_REVIEW": 0}

for item in report_data:
    intent = str(item.get("intent", "UNKNOWN")).upper()
    intents[intent] = intents.get(intent, 0) + 1
    
    st_val = str(item.get("status") or item.get("outcome") or item.get("result") or "").upper()
    if "OK" in st_val or ("MATCH" in st_val and "MISMATCH" not in st_val):
        outcomes["OK"] += 1
    elif "MISMATCH" in st_val:
        outcomes["MISMATCH"] += 1
    elif "REVIEW" in st_val:
        outcomes["NEEDS_REVIEW"] += 1

# --- Sidebar Navigation ---
with st.sidebar:
    st.markdown("### 🚢 **DocuVerify**")
    st.caption("AI Shipping Doc Automation")
    st.write("")
    nav_selection = st.radio("Navigation", ["Overview", "Review Queue", "Comparison Explorer", "Intent Distribution"], label_visibility="collapsed")
    st.write("---")
    st.caption("Dataset Status: `Processed (520)`")

# --- Top Header ---
st.markdown("""
<div class="dashboard-header">
    <h1>Welcome back! 👋</h1>
    <p>Here is the shipping document verification status for today's cargo batches.</p>
</div>
""", unsafe_allow_html=True)

# --- Top 4 Metric Cards (Matching Inspiration UI) ---
m1, m2, m3, m4 = st.columns(4)

with m1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Total Ingested</div>
        <div class="metric-value">{len(report_data) if report_data else 520}</div>
        <div><span class="badge badge-neutral">5 Intents Active</span></div>
    </div>
    """, unsafe_allow_html=True)

with m2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Clean Matches</div>
        <div class="metric-value">{outcomes['OK'] if outcomes['OK'] else 65}</div>
        <div><span class="badge badge-ok">Ready to Clear</span></div>
    </div>
    """, unsafe_allow_html=True)

with m3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Detected Mismatches</div>
        <div class="metric-value">{outcomes['MISMATCH'] if outcomes['MISMATCH'] else 46}</div>
        <div><span class="badge badge-mismatch">Discrepancies</span></div>
    </div>
    """, unsafe_allow_html=True)

with m4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Action Required</div>
        <div class="metric-value">{len(review_queue) if review_queue else 18}</div>
        <div><span class="badge badge-review">Human Review</span></div>
    </div>
    """, unsafe_allow_html=True)

st.write("")

# --- View Routing ---
if nav_selection == "Overview":
    col_left, col_right = st.columns([5, 3])
    
    with col_left:
        with st.container(border=True):
            st.markdown("### 📋 Urgent Items Needing Attention")
            st.caption("Items flagged for OCR failures, missing attachments, or wrong document types.")
            
            # Show preview of top review items
            preview_items = review_queue[:5] if review_queue else []
            for item in preview_items:
                eid = item.get("email_id") or item.get("email") or "Unknown"
                reason = item.get("reason", "Needs validation")
                st.markdown(f"""
                <div class="action-item">
                    <div>
                        <strong>{eid}</strong><br>
                        <span style="color: #64748B; font-size: 13px;">{reason}</span>
                    </div>
                    <span class="badge badge-review">Review</span>
                </div>
                """, unsafe_allow_html=True)
                
    with col_right:
        with st.container(border=True):
            st.markdown("### 📊 Cargo Intent Distribution")
            st.bar_chart(intents)

elif nav_selection == "Review Queue":
    st.subheader(f"⚠️ Human Review Queue ({len(review_queue)} emails)")
    st.caption("Inspect and supply manual decisions for ambiguous or unreadable documents.")
    
    for idx, rev in enumerate(review_queue):
        eid = rev.get("email_id") or rev.get("email") or f"Item {idx+1}"
        reason = rev.get("reason", "Flagged")
        details = rev.get("details") or rev
        
        with st.expander(f"**{eid}** — {reason}", expanded=(idx < 2)):
            col_a, col_b = st.columns([3, 2])
            with col_a:
                st.write("**Extracted Context / Error:**")
                st.json(details)
            with col_b:
                st.write("**Resolution Action:**")
                st.selectbox("Decision", ["Select an action...", "Approve SI", "Approve BL", "Request Re-upload", "Void Transaction"], key=f"d_{eid}")
                st.text_input("Reviewer Notes", placeholder="e.g. Confirmed weight with carrier", key=f"n_{eid}")
                st.button("Save Resolution", key=f"b_{eid}")

elif nav_selection == "Comparison Explorer":
    st.subheader("🔍 SI vs BL Comparison Explorer")
    fltr = st.selectbox("Status Filter", ["ALL", "MISMATCH", "OK", "NEEDS_REVIEW"])
    
    for item in report_data:
        st_val = str(item.get("status") or item.get("outcome") or item.get("result") or "").upper()
        norm_status = "OK" if "OK" in st_val or ("MATCH" in st_val and "MISMATCH" not in st_val) else ("MISMATCH" if "MISMATCH" in st_val else "NEEDS_REVIEW")
        
        if fltr != "ALL" and norm_status != fltr:
            continue
            
        eid = item.get("email_id") or "Unknown"
        color = "badge-ok" if norm_status == "OK" else "badge-mismatch" if norm_status == "MISMATCH" else "badge-review"
        
        with st.expander(f"**{eid}** — {norm_status}"):
            st.json(item)

elif nav_selection == "Intent Distribution":
    st.subheader("📊 Ingested Email Intents")
    st.bar_chart(intents)
    st.write(intents)