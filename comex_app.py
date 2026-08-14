import streamlit as st

st.set_page_config(page_title="Comex - Painel Global", page_icon="🌍", layout="wide")

st.markdown(
    """
    <h1 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
        Dashboard COMEX Internacional
    </h1>
    """,
    unsafe_allow_html=True,
)

st.write("")
st.markdown(
    "<p style='text-align:center; font-size:18px;'>Selecione o país para explorar os dados de comércio exterior:</p>",
    unsafe_allow_html=True,
)
st.write("")

col1, col2 = st.columns(2)

with col1:
    st.markdown("### 🇧🇷 Brasil")
    st.write(
        "Importações por URF, NCM/Posição/Capítulo, com tratamento de "
        "outliers e projeção de volume (fonte: Comex Stat / MDIC)."
    )
    st.page_link(
        "pages/1_🇧🇷_Comex_Brasil.py",
        label="Abrir painel do Brasil",
        icon="🇧🇷",
    )

with col2:
    st.markdown("### 🇺🇸 Estados Unidos")
    st.write(
        "Importações por código HTS, país de origem e ano "
        "(fonte oficial: USITC DataWeb)."
    )
    st.page_link(
        "pages/2_🇺🇸_Comex_EUA.py",
        label="Abrir painel dos EUA",
        icon="🇺🇸",
    )

st.write("")
st.caption(
    "Cada painel é independente: os filtros e dados de um país não afetam o outro."
)
