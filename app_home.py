import streamlit as st

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

# Altura fixa para o bloco de título+descrição, garantindo que os botões
# fiquem sempre alinhados na mesma linha, independente do tamanho do texto.
BLOCK_HEIGHT = 170

col1, col2 = st.columns(2)

with col1:
    with st.container(height=BLOCK_HEIGHT, border=False):
        st.markdown(
            f"""
            <h3><img src="{FLAG_BR}" width="28" style="vertical-align:middle; margin-right:8px;">Brasil</h3>
            """,
            unsafe_allow_html=True,
        )
        st.write(
            "Importações por URF, NCM/Posição/Capítulo, com tratamento de "
            "outliers e projeção de volume (fonte: Comex Stat / MDIC)."
        )
    st.page_link(
        "pages/1_Comex_Brasil.py",
        label="Abrir painel do Brasil",
        use_container_width=True,
    )

with col2:
    with st.container(height=BLOCK_HEIGHT, border=False):
        st.markdown(
            f"""
            <h3><img src="{FLAG_US}" width="28" style="vertical-align:middle; margin-right:8px;">Estados Unidos</h3>
            """,
            unsafe_allow_html=True,
        )
        st.write(
            "Importações por código HTS, país de origem e ano "
            "(fonte oficial: USITC DataWeb)."
        )
    st.page_link(
        "pages/2_Comex_EUA.py",
        label="Abrir painel dos EUA",
        use_container_width=True,
    )

st.write("")
st.caption(
    "Cada painel é independente: os filtros e dados de um país não afetam o outro."
)
