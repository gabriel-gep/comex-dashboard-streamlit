import streamlit as st

home_page = st.Page("app_home.py", title="Menu", icon="🧭", default=True)
brasil_page = st.Page(
    "pages/1_Comex_Brasil.py", title="Comex Brasil", url_path="comex-brasil"
)
eua_page = st.Page(
    "pages/2_Comex_EUA.py", title="Comex EUA", url_path="comex-eua"
)

pg = st.navigation([home_page, brasil_page, eua_page])
pg.run()
