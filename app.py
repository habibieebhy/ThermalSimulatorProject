"""Programmatic Streamlit navigation for BRIXTA Evidence Discovery."""

from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

st.set_page_config(
    page_title="BRIXTA Evidence Discovery",
    page_icon="",
    layout="wide",
)

navigation = st.navigation(
    [
        st.Page("pages/0_Home.py", title="Home", default=True),
        st.Page(
            "pages/1_Evidence_Collection_Lab.py",
            title="Evidence Collection Lab",
        ),
        st.Page(
            "pages/2_Open_Research_Campaign.py",
            title="Open Research Campaign",
        ),
        st.Page(
            "pages/3_Cement_Evidence_Intake.py",
            title="Cement Evidence Intake",
        ),
        st.Page(
            "pages/4_Database_Explorer.py",
            title="Database Explorer",
        ),
    ]
)
navigation.run()
