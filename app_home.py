import streamlit as st

st.set_page_config(page_title="Comex - Menu", page_icon="🌍", layout="wide")

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

FLAG_BR = "https://flagcdn.com/w40/br.png"
FLAG_US = "https://flagcdn.com/w40/us.png"

# CSS do card inteiro clicável (evita usar tags <h1>-<h6>, que o Streamlit
# transforma automaticamente em títulos com ícone de âncora).
st.markdown(
    """
    <style>
    .comex-card {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 230px;
        border: 1px solid #d0d7de;
        border-radius: 10px;
        padding: 20px 22px;
        text-decoration: none !important;
        color: inherit !important;
        transition: box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .comex-card:hover {
        box-shadow: 0 3px 12px rgba(4, 35, 115, 0.18);
        border-color: #042373;
    }
    .comex-card-title {
        font-size: 1.4rem;
        font-weight: 700;
        margin-bottom: 0.6rem;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .comex-card-desc {
        font-size: 0.95rem;
        color: #31333F;
        line-height: 1.5;
    }
    .comex-card-cta {
        margin-top: 18px;
        font-weight: 600;
        color: #042373;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

col1, col2 = st.columns(2)

with col1:
    st.markdown(
        f"""
        <a class="comex-card" href="comex-brasil" target="_self">
            <div>
                <div class="comex-card-title">
                    <img src="{FLAG_BR}" width="28">Brasil
                </div>
                <div class="comex-card-desc">
                    Importações por URF, NCM/Posição/Capítulo, com tratamento de
                    outliers e projeção de volume (fonte: Comex Stat / MDIC).
                </div>
            </div>
            <div class="comex-card-cta">Abrir painel do Brasil →</div>
        </a>
        """,
        unsafe_allow_html=True,
    )

with col2:
    st.markdown(
        f"""
        <a class="comex-card" href="comex-eua" target="_self">
            <div>
                <div class="comex-card-title">
                    <img src="{FLAG_US}" width="28">Estados Unidos
                </div>
                <div class="comex-card-desc">
                    Importações por código HTS, país de origem e ano
                    (fonte oficial: USITC DataWeb).
                </div>
            </div>
            <div class="comex-card-cta">Abrir painel dos EUA →</div>
        </a>
        """,
        unsafe_allow_html=True,
    )

st.write("")
st.caption(
    "Cada painel é independente: os filtros e dados de um país não afetam o outro."
)
