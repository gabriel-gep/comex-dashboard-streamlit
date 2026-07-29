import streamlit as st
import requests
import pandas as pd
import certifi
import numpy as np
from statsforecast import StatsForecast
from statsforecast.models import ETS
import datetime

from PIL import Image
import os

# Abre a imagem
#img = Image.open("imagens/Logo.png")



st.set_page_config(page_title="Comex", page_icon="🌍", layout="wide")


#st.sidebar.image(img)
#st.image("Logo.png", use_container_width=True)
st.sidebar.header("🔍 Filtros")

# URL da API
url = "https://api-comexstat.mdic.gov.br/general"


#current_working_dir = os.getcwd()
#st.code(current_working_dir)

st.markdown(
    """
    <h1 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
        Dashboard COMEX Importações por URF
    </h1>
    """,
    unsafe_allow_html=True
)

st.markdown("""
### Utilização do Aplicativo

1. **Escolha o tipo de classificação para buscar os dados:** NCM (8 digitos da Nomenclatura Mercosul), Posição (4 primeiros digitos do NCM), Capítulo (2 primeiros digitos NCM)
2. Selecione o ano inicial desejado *(Se houver muitos dados, o sistema vai automaticamente avançar para o ano seguinte até conseguir carregar)*
3. Caso queira adicionar mais NCMs, posições ou capítulos, clique no símbolo de mais (+) para adicioná-los.
4. Clique em **Buscar Dados** SEMPRE que quiser carregar ou atualizar as visualizações.
""")

# Input do usuário
#heading = st.sidebar.text_input("Digite o código do Heading (ex: 2901):", "2901")
filtro_selecionado = st.sidebar.radio(
    "Selecione o tipo de filtro desejado:",
    ["NCM (completo)", "Posição (4 primeiros digitos)", "Capítulo(2 primeiros digitos)"],
    horizontal=False # Isso coloca as opções lado a lado no topo
)


ano_atual = datetime.datetime.now().year
anos = range(2020, ano_atual)
anos_cap = range(ano_atual - 2, ano_atual +1)

if filtro_selecionado == "Posição (4 primeiros digitos)":
    ano_inicial = st.sidebar.selectbox("Selecione o ano inicial", anos)
    
    filtro = "heading"
    st.sidebar.write("Digite os códigos de Posição:")
    num_fields = st.sidebar.number_input("Quantas Posições?", min_value=1, max_value=10, value=2, help="O máximo permitido são 10 posições")
    
    headings = []
    for i in range(num_fields):
        heading = st.sidebar.text_input(f"Posição {i+1}:", value="2901" if i == 0 else "", max_chars= 4)
        if heading and heading.isdigit():
            headings.append(int(heading))
    
    if headings:
        api_param = headings
        #st.sidebar.success(f"Parâmetro para API: {api_param}")

elif filtro_selecionado == "NCM (completo)":
    ano_inicial = st.sidebar.selectbox("Selecione o ano inicial", anos)
    
    filtro = "ncm"
    st.sidebar.write("Digite os códigos do NCM:")
    num_fields = st.sidebar.number_input("Quantos NCMs?", min_value=1, max_value=20, value=2, help = "O máximo permitido são 20 NCMs")
    
    ncms = []
    for i in range(num_fields):
        ncm = st.sidebar.text_input(f"NCM {i+1}:", value="29339999" if i == 0 else "", max_chars= 8)
        if ncm and ncm.isdigit():
            ncms.append(int(ncm))
    
    if ncms:
        api_param = ncms
        #st.sidebar.success(f"Parâmetro para API: {api_param}")

elif filtro_selecionado == "Capítulo(2 primeiros digitos)":
    ano_inicial = st.sidebar.selectbox("Selecione o ano inicial", anos_cap)
    
    filtro = "chapter"
    st.sidebar.write("Digite os códigos dos capítulos:")
    num_fields = st.sidebar.number_input("Quantos capítulos?", min_value=1, max_value=3, value=1, help = "O máximo permitido são 3 capítulos")
    
    chapters = []
    for i in range(num_fields):
        chapter = st.sidebar.text_input(f"Chapter {i+1}:", value="81" if i == 0 else "", max_chars= 2)
        if chapter and chapter.isdigit():
            chapters.append(int(chapter))
    
    if chapters:
        api_param = chapters
        #st.sidebar.success(f"Parâmetro para API: {api_param}")

# Área principal
#st.write(f"Data selecionada: {data_formatada}")
#st.write(f"Filtro selecionado: {filtro_selecionado.upper()}")

# Aqui você pode adicionar a lógica específica para cada tipo de filtro
#if 'headings' in locals() and headings:
    #st.write(f"Headings selecionados: {headings}")
#elif 'ncms' in locals() and ncms:
   # st.write(f"NCMs selecionados: {ncms}")
#elif 'chapters' in locals() and chapters:
    #st.write(f"Chapters selecionados: {chapters}")




def pegar_api_por_ano(url, filtro, api_param, ano_inicial, ano_atual):

    body = {
        "flow": "import",
        "monthDetail": True,
        "period": {"from": f"{ano_inicial}-01", "to": f"{ano_atual}-12"},
        "filters": [{"filter": filtro, "values": api_param}],
        "details": ["country", "ncm", "urf", "via"],
        "metrics": ["metricFOB", "metricKG", "metricStatistic",
                    "metricFreight", "metricInsurance", "metricCIF"]
    }


    try:
        res = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            verify=False
        )

        if res.status_code == 200:
            dados = res.json()["data"]["list"]
            df = pd.DataFrame(dados)
            return df, 200

        return None, res.status_code

    except Exception as e:
        st.error(f"Erro de conexão: {e}")
        return None, -1

from datetime import datetime

def tentar_anos(url, filtro, api_param, ano_inicial, ano_atual):
    ano_atual = datetime.now().year

    for ano in range(ano_inicial, ano_atual + 1):
        st.write(f"Tentando ano {ano}...")

        df, status = pegar_api_por_ano(url = url, 
                                       filtro=filtro, 
                                       api_param = api_param, 
                                       ano_inicial= ano,
                                       ano_atual = ano_atual)
           
        if status == 200:
            st.success(f"Dados carregados a partir do ano {ano}")
            st.session_state.df = df
            return df, ano

        elif status == 500:
            st.warning("Muitos dados para esse período, tentando próximo ano...")
            continue

        elif status == 429:
            st.warning("Limite excedido. Tente novamente em 10 segundos.")
            return None, ano

        else:
            st.error(f"Erro {status}")
            return None, ano

    st.error("Não foi possível carregar os dados até o ano atual.")
    st.write("Dica: Tente novamente com uma solicitação menor. Reduza o número de itens pesquisados ou altere o tipo de classificação para uma opção mais específica.")

    return None, None

if st.sidebar.button("Buscar dados"):

    # Limpa o pipeline derivado para forçar recálculo com os novos dados
    chaves_para_limpar = [
        "df1", "df_2", "base_urf", "df_wide_metade_zeros",
        "forecast_df1", "df_completo", "df_final",
        "df_long", "df_long_final", "tabela_combinada"
    ]
    for chave in chaves_para_limpar:
        if chave in st.session_state:
            del st.session_state[chave]

    df, ano_usado = tentar_anos(url, filtro, api_param, ano_inicial, ano_atual)

    if df is not None:
        st.write(f"Consulta realizada usando o ano: {ano_usado}")

def preencher_nan_entre_dados(df, coluna_data='data'):
    """
    Preenche NaN com 0 apenas entre o primeiro e último valor não-NaN de cada série
    Mantém NaN antes do primeiro valor e depois do último valor
    """
    df_resultado = df.copy()
    
    # Identificar colunas numéricas (excluindo a coluna de data)
    colunas_numericas = df.select_dtypes(include=[np.number]).columns
    if coluna_data in colunas_numericas:
        colunas_numericas = colunas_numericas.drop(coluna_data)
    
    for coluna in colunas_numericas:
        # Encontrar índices do primeiro e último valor não-NaN
        indices_nao_nan = df[coluna].notna()
        if indices_nao_nan.any():
            primeiro_idx = indices_nao_nan.idxmax()
            ultimo_idx = indices_nao_nan[::-1].idxmax()
            
            # Preencher NaN com 0 apenas entre o primeiro e último valor não-NaN
            mascara_entre_dados = (df.index >= primeiro_idx) & (df.index <= ultimo_idx)
            df_resultado.loc[mascara_entre_dados, coluna] = df_resultado.loc[mascara_entre_dados, coluna].fillna(0)
    
    return df_resultado

def remover_colunas_zeros_na_ultimos_6_meses(df, col_data="data"):
    # Identificar colunas numéricas (excluindo a coluna de data)
    colunas_numericas = df.select_dtypes(include=[np.number]).columns.tolist()
    
    # Colunas a remover: aquelas em que os últimos 6 valores são zero ou NA
    colunas_remover = []
    
    for col in colunas_numericas:
        valores = df[col]
        n = len(valores)
        
        if n >= 6:
            ultimos_6 = valores.tail(6)
            # Verificar se todos os últimos 6 valores são zero OU são NA
            if ((ultimos_6 == 0) | (ultimos_6.isna())).all():
                colunas_remover.append(col)
        else:
            # Se tiver menos de 6 linhas, verificar se todos são zero ou NA
            if ((valores == 0) | (valores.isna())).all():
                colunas_remover.append(col)
    
    # Manter coluna de data + colunas numéricas que NÃO estão na lista de remoção
    colunas_manter = [col_data] + [col for col in colunas_numericas if col not in colunas_remover]
    
    return df[colunas_manter]

def remover_colunas_metade_zeros(df, col_data="data"):
    # Identificar colunas numéricas
    if col_data is None:
        colunas_numericas = df.columns.tolist()
    else:
        colunas_numericas = df.select_dtypes(include=[np.number]).columns.tolist()
    
    if len(colunas_numericas) == 0:
        return df
    
    # Calcular proporção de NA e zeros em cada coluna
    proporcao_na_zeros = {}
    for col in colunas_numericas:
        # Contar NA e zeros
        na_count = df[col].isna().sum()
        zero_count = (df[col] == 0).sum()
        total_count = len(df[col])
        
        # Calcular proporção combinada de NA e zeros
        proporcao_na_zeros[col] = (na_count + zero_count) / total_count
    
    # Manter colunas com menos de 30% de NA ou zeros
    colunas_manter_numericas = [col for col in colunas_numericas if proporcao_na_zeros[col] < 0.3]
    
    # Definir colunas finais a manter
    if col_data is None:
        colunas_manter = colunas_manter_numericas
    else:
        colunas_manter = [col_data] + colunas_manter_numericas
    
    return df[colunas_manter]

def forecast_ets_mnm_robust(df, date_col, h=6, constant=1e-6, max_retries=2):
    """
    Versão com tratamento robusto de erros e datas de projeção corretas para cada série
    com compressão suave para valores projetados acima do máximo histórico
    """
    
    ts_columns = [col for col in df.columns if col != date_col]
    all_forecasts = []
    
    for col in ts_columns:
        for attempt in range(max_retries + 1):
            try:
                print(f"Processando série: {col} (tentativa {attempt + 1})")
                
                # Preparar dados para esta série específica
                temp_df = df[[date_col, col]].copy()
                temp_df = temp_df.dropna()
                
                if len(temp_df) < 2:
                    print(f"Série {col} tem dados insuficientes")
                    break
                
                temp_df = temp_df.rename(columns={date_col: 'ds', col: 'y'})
                temp_df['unique_id'] = col
                temp_df['ds'] = pd.to_datetime(temp_df['ds'])
                
                # Calcular máximo histórico ANTES de aplicar a constante
                max_historico = temp_df['y'].max()
                
                # Aplicar constante
                temp_df['y'] = temp_df['y'] + constant
                
                # Encontrar a última data REAL desta série (não do DataFrame inteiro)
                last_date_series = temp_df['ds'].max()
                
                # Configurar modelo
                model = ETS(model='MNM', season_length=12)
                
                # StatsForecast
                sf = StatsForecast(
                    models=[model],
                    freq='ME',
                    n_jobs=1
                )
                
                # Fazer previsão
                forecast = sf.forecast(df=temp_df, h=h)
                
                # Aplicar compressão suave nos valores previstos
                forecast_values = forecast['ETS'].values - constant  # Remover constante
                
                # Aplicar a transformação de compressão suave
                forecast_ajustado = []
                for valor in forecast_values:
                    if valor > max_historico:
                        # Fórmula de compressão suave (equivalente ao código R)
                        valor_ajustado = max_historico + (max_historico * 0.1) * (1 - np.exp(-(valor - max_historico) / (max_historico * 0.2)))
                        forecast_ajustado.append(valor_ajustado)
                    else:
                        forecast_ajustado.append(valor)
                
                # Criar DataFrame para esta série com datas corretas
                forecast_dates = pd.date_range(
                    start=last_date_series + pd.DateOffset(months=1),
                    periods=h,
                    freq='ME'
                )
                
                forecast_series = pd.DataFrame({
                    'ds': forecast_dates,
                    col: forecast_ajustado,  # Usar valores ajustados
                    'unique_id': col
                })
                
                all_forecasts.append(forecast_series)
                print(f"Série {col} processada com sucesso. Última data: {last_date_series}")
                break
                
            except Exception as e:
                print(f"Erro na série {col}, tentativa {attempt + 1}: {e}")
                if attempt == max_retries:
                    # Criar série com NAs mas com datas baseadas na última data disponível
                    try:
                        temp_df = df[[date_col, col]].copy().dropna()
                        if len(temp_df) > 0:
                            last_date_series = pd.to_datetime(temp_df[date_col].max())
                            forecast_dates = pd.date_range(
                                start=last_date_series + pd.DateOffset(months=1),
                                periods=h,
                                freq='ME'
                            )
                            forecast_series = pd.DataFrame({
                                'ds': forecast_dates,
                                col: np.full(h, np.nan),
                                'unique_id': col
                            })
                            all_forecasts.append(forecast_series)
                    except:
                        pass
                continue
    
    if not all_forecasts:
        return pd.DataFrame()
        
    # Consolidar todas as previsões
    all_dates = set()
    for f in all_forecasts:
        all_dates.update(f['ds'].tolist())
    
    all_dates = sorted(list(all_dates))
    consolidated_df = pd.DataFrame({'ds': all_dates})
    
    # Para cada série, fazer merge com o DataFrame consolidado
    for forecast_df in all_forecasts:
        col_name = forecast_df['unique_id'].iloc[0]
        # Manter apenas as colunas necessárias
        series_forecast = forecast_df[['ds', col_name]].copy()
        consolidated_df = consolidated_df.merge(
            series_forecast, 
            on='ds', 
            how='left'
        )
    
    # Definir a data como índice
    consolidated_df = consolidated_df.set_index('ds')
    
    return consolidated_df

# 3. Adicionar coluna Tipo baseada na data de corte de cada série
def classificar_tipo(row):
    if pd.isna(row['valor']):
        return None
    
    data_corte = datas_corte.get(row['serie'])
    
    # Se não encontrou data de corte para esta série, verificar se existe no dataframe original
    if data_corte is None:
        # Se a série existe no dataframe original mas todos valores são nulos, considerar como projetado
        if row['serie'] in df_wide_metade_zeros.columns:
            return 'Projetado'
        else:
            return 'Projetado'  # Série que só existe nas projeções
    
    # Classificar baseado na data
    if row['data'] <= data_corte:
        return 'Realizado'
    else:
        return 'Projetado'
    

    # Função para quebrar título longo
def quebrar_titulo(texto, max_caracteres=20):
    if len(texto) <= max_caracteres:
        return texto
    palavras = texto.split()
    linhas = []
    linha_atual = ""
    
    for palavra in palavras:
        if len(linha_atual + " " + palavra) <= max_caracteres:
            linha_atual += " " + palavra if linha_atual else palavra
        else:
            if linha_atual:
                linhas.append(linha_atual)
            linha_atual = palavra
    
    if linha_atual:
        linhas.append(linha_atual)
    
    return "<br>".join(linhas)

if "df" in st.session_state:
    if "df1" not in st.session_state:
        df1 = st.session_state.df.copy()
        df1["IE"] = "Import"
        df1[['urf_code', 'urf']] = df1['urf'].str.split(' - ', n=1, expand=True)
        df1 = df1.assign(
        data=lambda x: pd.to_datetime(
        x['year'].astype(str) + '-' + x['monthNumber'].astype(str) + '-01'
        ).dt.date
        )
        df1['Tipo'] = "Realizado"
        st.session_state.df1 = df1
    else:
        df1  = st.session_state.df1
    

    if "df_2" not in st.session_state:
        df_2 = df1.loc[:, ['coNcm', 'metricFOB', 'data','urf','metricStatistic', 'via', 'country']]
        df_2['metricStatistic'] = df_2['metricStatistic'].astype('float64')
        st.session_state.df_2 = df_2
    else:
        df_2  = st.session_state.df_2


    if "base_urf" not in st.session_state:

        base_urf = df1.loc[:,['urf_code','urf']].drop_duplicates()
        base_urf = base_urf.assign(urf_completo = base_urf['urf_code'].astype(str) + ' - ' + base_urf['urf'].astype(str))
        #st.write(base_urf)
        st.session_state.base_urf = base_urf
    else:
        base_urf  = st.session_state.base_urf

       

    if "df_wide_metade_zeros" not in st.session_state:

        df_wide = (df_2
               .assign(column_name=lambda x: x['coNcm'] + '_' + x['urf'])
                .pivot_table(
                    index='data',
                    columns='column_name',
                    values='metricStatistic', 
                    aggfunc='first'
                )
                .reset_index()
                .sort_values('data'))
    
        df_wide_2 = preencher_nan_entre_dados(df_wide, 'data')
    
        df_wide_6meses = remover_colunas_zeros_na_ultimos_6_meses(df_wide_2)
    

        df_wide_metade_zeros = remover_colunas_metade_zeros(df_wide_6meses)
        df_wide_metade_zeros['data'] = pd.to_datetime(df_wide_metade_zeros['data']).dt.date
        st.session_state.df_wide_metade_zeros = df_wide_metade_zeros
    else:
        df_wide_metade_zeros  = st.session_state.df_wide_metade_zeros
    
    #st.write(df_wide_metade_zeros)

    st.success("✅ Dados foram tratados!")

    if "forecast_df1" not in st.session_state:
        forecast_df1 = forecast_ets_mnm_robust(df_wide_metade_zeros, date_col='data', h=6, constant=1e-6)
        forecast_df1= forecast_df1.reset_index(names ='data')  
        forecast_df1['data'] = pd.to_datetime(forecast_df1['data']).dt.date
        st.session_state.forecast_df1 = forecast_df1
    else:
        forecast_df1 = st.session_state.forecast_df1 

    st.success("✅ Projeção foi Realizada!")

    if "df_completo" not in st.session_state:
        df_completo = pd.concat([df_wide_metade_zeros, forecast_df1], ignore_index=True)
        st.session_state.df_completo = df_completo
    else:
        df_completo = st.session_state.df_completo  

    if "df_final" not in st.session_state:
        df_final = df_completo.groupby('data', as_index=False).first()
        df_final = df_final.sort_values('data').reset_index(drop=True)
        st.session_state.df_final = df_final
    else:
        df_final = st.session_state.df_final

    # 1. Encontrar as datas de corte para cada série a partir dos dataframes originais
     
    datas_corte = {}

        # Para cada série, encontrar a última data com valor realizado
    for coluna in df_wide_metade_zeros.columns:
        if coluna != 'data':
            # Filtrar apenas linhas não nulas no dataframe original (realizado)
            
            serie_realizada = df_wide_metade_zeros[df_wide_metade_zeros[coluna].notna()]

            if not serie_realizada.empty:
            # Encontrar a última data com valor realizado
                datas_corte[coluna] = serie_realizada['data'].max()


    if "df_long" not in st.session_state:
        df_long = df_final.melt(id_vars=['data'], var_name='serie', value_name='valor')
        df_long['Tipo'] = df_long.apply(classificar_tipo, axis=1)

        df_long = df_long.dropna(subset=['valor'])
        df_long = df_long.sort_values(['serie', 'data']).reset_index(drop=True)
        df_long[['coNcm', 'urf']] = df_long['serie'].str.split('_', n=1, expand=True)
        st.session_state.df_long = df_long
    else:
        df_long = st.session_state.df_long

    if "df_long_final" not in st.session_state:
        colunas_ordenadas = ['data', 'coNcm', 'urf', 'valor', 'Tipo']

        df_long_final = st.session_state.df_long[colunas_ordenadas]
        df_long_final = df_long_final.sort_values(['coNcm', 'urf', 'data']).reset_index(drop=True)
        st.session_state.df_long_final = df_long_final
    else:
        df_long_final = st.session_state.df_long_final



    # cria a tabela só uma vez
    if "tabela_combinada" not in st.session_state:
        tabela_combinada = pd.merge(
        st.session_state.df_long_final, 
        st.session_state.base_urf, 
        on="urf", 
        how="inner"
    )
        st.session_state.tabela_combinada = tabela_combinada
    else:
        tabela_combinada = st.session_state.tabela_combinada

    st.markdown(
    """
    <div style="
        background-color:#f9f9f9;
        border-left: 6px solid #ffcc00;
        padding: 15px;
        margin-bottom: 20px;
        font-size: 14px;
    ">
    ⚠️ <b>Disclaimer:</b> Os dados apresentados passaram por tratamento de outliers e, portanto, 
    podem não refletir integralmente a realidade observada. Além disso, as informações dependem da 
    disponibilidade e do histórico de dados fornecidos pela API do Comex Stat.
    </div>
    """,
    unsafe_allow_html=True
    )

    #st.write(df1)
    #st.write(tabela_combinada)
    # filtro de NCM
    NCM = st.selectbox(
    "NCM", 
    st.session_state.tabela_combinada["coNcm"].unique()
    )

    datas = pd.to_datetime(df1['data']).sort_values()

    
    # Range total do slider
    data_min_slider = datas.min()
    data_max_slider = datas.max()

    data_default_inicio = data_max_slider - pd.DateOffset(months=12)
    if data_default_inicio < data_min_slider:
        data_default_inicio = data_min_slider
    
    df1_filtrado = df1[df1["coNcm"] == NCM]
    
    

    df1_filtrado2 = df1_filtrado.loc[:,['coNcm','ncm']]
    df1_filtrado2 = df1_filtrado2.drop_duplicates()

    lista_ncms = [f"{row['coNcm']} - {row['ncm']}" for _, row in df1_filtrado2.iterrows()]

    texto_ncm = ", ".join(lista_ncms)

    # Mostra o texto no Streamlit
    st.write(f"**NCM selecionado:** {texto_ncm}")

    #st.write(df1_filtrado2)

    tabela_combinada['data'] = pd.to_datetime(tabela_combinada['data']).dt.to_period('M').dt.to_timestamp().dt.date
    #tabela_combinada['valor'] = tabela_combinada['valor'].round()

    #st.write(tabela_combinada)


    import pandas as pd

    dados_filtrados = st.session_state.tabela_combinada[
    st.session_state.tabela_combinada["coNcm"] == NCM]
    #st.write(dados_filtrados)

    dados_filtrados_2 = dados_filtrados.loc[:,['data','coNcm','valor','urf_completo', 'Tipo']]


    dados_filtrados_2.columns = ['Data','NCM','Volume','URF', 'Tipo']

    from st_aggrid import AgGrid, GridOptionsBuilder

    gb = GridOptionsBuilder.from_dataframe(dados_filtrados_2)
    gb.configure_default_column(filter=True, sortable=True, floatingFilter=True)
    AgGrid(dados_filtrados_2, gridOptions=gb.build(), fit_columns_on_grid_load=True)

    dados_filtrados['data'] = pd.to_datetime(dados_filtrados['data'], errors='coerce')

    urfs_grafico = dados_filtrados['urf'].unique()

    #st.write(urfs_grafico)
    #st.write(df1)
    # converter as colunas para numéricas (forçando erros a virar NaN)
    df1['metricFOB'] = pd.to_numeric(df1['metricFOB'], errors='coerce')
    df1['metricStatistic'] = pd.to_numeric(df1['metricStatistic'], errors='coerce')
    
    df1['data'] = pd.to_datetime(df1['data'])  # garante que é datetime
    df1['ano'] = df1['data'].dt.year 
    # calcular a nova coluna
    df1['precoFOB'] = np.where(
    (df1['metricStatistic'].notna()) & (df1['metricStatistic'] != 0),
    df1['metricFOB'] / df1['metricStatistic'],
    np.nan
    )

    mediana_urf_ano = df1.groupby(['urf', 'ano'])['precoFOB'].transform('median')

    # 3️⃣ Substituir apenas se metricStatistic == 0 e metricFOB != 0
    mask_substituir = (df1['metricStatistic'] == 0) & (df1['metricFOB'] != 0)
    df1.loc[mask_substituir, 'precoFOB'] = mediana_urf_ano[mask_substituir]

    # 4️⃣ Se quiser, manter precoFOB = 0 quando ambos forem zero
    mask_zeros = (df1['metricStatistic'] == 0) & (df1['metricFOB'] == 0)
    df1.loc[mask_zeros, 'precoFOB'] = 0
    
    grp = df1.groupby(['urf', 'ano'])['precoFOB']
    
    q1 = grp.transform(lambda x: x.quantile(0.25))
    q3 = grp.transform(lambda x: x.quantile(0.75))
    iqr = q3 - q1
    lim_inf = q1 - 1.5 * iqr
    lim_sup = q3 + 1.5 * iqr
    mediana = grp.transform('median')
    
    mask_outlier = (df1['precoFOB'] < lim_inf) | (df1['precoFOB'] > lim_sup)
    df1.loc[mask_outlier, 'precoFOB'] = mediana[mask_outlier]

    df1['data'] = pd.to_datetime(df1['data'])

    #st.write(df1)
    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
        Ranking de preço Médio por URF
        <span title="Outliers foram tratados">ⓘ</span>
        </h2>
        """,
        unsafe_allow_html=True)


    

    # --- Definir o range do slider com base nas das da base ---
    min_data_df1 = df1['data'].min()
    max_data_df1 = df1['data'].max()

    # Slider para selecionar o período
    periodo = st.slider(
        "Selecione o período:",
        min_value=min_data_df1.to_pydatetime(),
        max_value=max_data_df1.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max_slider.to_pydatetime()),
        format="DD/MM/YYYY"
    )

    # --- Filtrar o DataFrame pelo período selecionado ---
    df_filtrado_df1 = df1[(df1['data'] >= periodo[0]) & (df1['data'] <= periodo[1])]

    df_filtrado_df1 = df_filtrado_df1[
        (df_filtrado_df1['urf'].isin(urfs_grafico))]

    # --- Calcular média de precoFOB por URF ---
    ranking = (
        df_filtrado_df1.groupby('urf', as_index=False)[['precoFOB']]
        .mean()
        .rename(columns={'precoFOB': 'precoFOB Medio'})
    )

    # --- Ordenar do maior para o menor ---
    ranking = ranking.sort_values('precoFOB Medio', ascending=True)

    # --- Adicionar ranking numérico ---
    ranking['rank'] = ranking['precoFOB Medio'].rank(ascending=True, method='dense').astype(int)
    urf_menor_preco = ranking.loc[
    ranking['precoFOB Medio'].rank(ascending=True, method='dense') == 1,
    'urf'
    ].iloc[0]

    # --- Mostrar resultado ---
    st.dataframe(ranking, hide_index=True)

    st.markdown( f""" <h4 style='text-align:center; color:#0845E0; font-family:Arial; 
    font-weight:bold;'> O URF com menor preço FOB médio no período selecionado é: <b>{urf_menor_preco}</b> </h4> """, unsafe_allow_html=True )
    



    # (Opcional) Gráfico de barras
    #st.bar_chart(ranking.set_index('urf')['precoFOB_medio'])

    


    

    import matplotlib.pyplot as plt

    import plotly.subplots as sp
    data_min = dados_filtrados['data'].min()
    data_max = dados_filtrados['data'].max()
    
    
    
    # Filtrar o DataFrame pelo intervalo selecionado
    


    urfs_grafico = dados_filtrados['urf'].unique()
    num_urfs = len(urfs_grafico)

    cols_por_linha = 2
    linhas = (num_urfs + cols_por_linha - 1) // cols_por_linha

    

    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Volume Importado em Unidades: Realizado vs Projetado
        </h2>
        """,
        unsafe_allow_html=True
        )
    
    intervalo_0 = st.slider(
        "Selecione o intervalo de datas:",
        min_value=data_min.to_pydatetime(),
        max_value=data_max.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max.to_pydatetime()),
        format="YYYY-MM",
        key="slider_barras_0"
    )

    mask_2 = (dados_filtrados['data'] >= intervalo_0[0]) & (dados_filtrados['data'] <= intervalo_0[1])
    dados_filtrados_0 = dados_filtrados.loc[mask_2].copy()

    # Criar figura com títulos quebrados
    fig = sp.make_subplots(
        rows=linhas, cols=cols_por_linha,
        subplot_titles=[f"URF: {quebrar_titulo(str(u))}" for u in urfs_grafico],
        vertical_spacing=0.15,   # ajuste do espaçamento
        horizontal_spacing=0.1   # espaço horizontal entre gráficos
    )

    # Restante do código permanece igual...
    for i, urf in enumerate(urfs_grafico):
        dados_urf = dados_filtrados_0[dados_filtrados_0['urf'] == urf]
    
        row = i // cols_por_linha + 1
        col = i % cols_por_linha + 1
    
        for tipo, cor in zip(["Realizado", "Projetado"], ["blue", "green"]):
            subset = dados_urf[dados_urf["Tipo"] == tipo]
            fig.add_scatter(
                x=subset["data"],
                y=subset["valor"],
                mode='lines+markers',
                name=f"{tipo} - {urf}",
                line=dict(color=cor),
                row=row, col=col,
                hovertemplate="Data: %{x}<br>Valor: %{y}<extra></extra>"
            )

        realizado_urf = dados_urf[dados_urf["Tipo"] == "Realizado"].sort_values("data")
        projetado_urf = dados_urf[dados_urf["Tipo"] == "Projetado"].sort_values("data")

        if not realizado_urf.empty and not projetado_urf.empty:
            fig.add_scatter(
                x=[realizado_urf["data"].iloc[-1], projetado_urf["data"].iloc[0]],
                y=[realizado_urf["valor"].iloc[-1], projetado_urf["valor"].iloc[0]],
                mode='lines',
                line=dict(color='gray', dash='dot'),
                showlegend=False,
                row=row, col=col,
                hoverinfo='skip'
            )

    # Layout com ajustes para os títulos
    fig.update_layout(
        height=400*linhas, 
        width=1200,  # aumentei a largura
        showlegend=False,
        plot_bgcolor="#DBF7FF",   # fundo azul claro da área externa
        paper_bgcolor="white",    # fora branco
        margin=dict(t=100, b=50, l=50, r=50)  # margens ajustadas
    )

    # Ajustar tamanho da fonte, cor e negrito dos títulos
    fig.update_annotations(
        font=dict(
            size=14,           # tamanho maior
            color="#042373",     # cor preta
            family="Arial",    # fonte (opcional)
            weight="bold"      # negrito
        ),
        yshift=10  # move títulos para cima
    )

    # ADICIONAR BORDAS AZUL ESCURO EM CADA GRÁFICO
    fig.update_xaxes(
        showgrid=False, 
        zeroline=False, 
        showline=True,                    # MOSTRAR LINHA DO EIXO
        linewidth=2,                      # ESPESSURA DA BORDA
        linecolor='#042373',             # COR AZUL ESCURO
        mirror=True,                      # REPETIR A LINHA EM TODOS OS LADOS
        showticklabels=True               # MANTER OS RÓTULOS VISÍVEIS
    )

    fig.update_yaxes(
        showgrid=False, 
        zeroline=False, 
        showline=True,                    # MOSTRAR LINHA DO EIXO
        linewidth=2,                      # ESPESSURA DA BORDA
        linecolor='#042373',             # COR AZUL ESCURO
        mirror=True,                      # REPETIR A LINHA EM TODOS OS LADOS
        showticklabels=True               # MANTER OS RÓTULOS VISÍVEIS
    )

    st.plotly_chart(fig, use_container_width=True)
    




    import streamlit as st
    import plotly.subplots as sp
    import plotly.graph_objects as go

    

    # Converter a coluna 'data' para datetime
    

    # Selecionar URFs que vamos mostrar
    
    dados_filtrados["valor"] = pd.to_numeric(dados_filtrados["valor"], errors="coerce")
    # Filtrar dados apenas para essas URFs e Tipo = "Realizado"
    df_filtrado_volume = dados_filtrados[
        (dados_filtrados['urf'].isin(urfs_grafico)) &
        (dados_filtrados['Tipo'] == 'Realizado')&
        (dados_filtrados["coNcm"] == NCM)
    ]

    # Agrupar por data e urf, somando o volume
    df_agrupado = (
        df_filtrado_volume
        .groupby(['data', 'urf'], as_index=False)['valor']
        .sum()
        .sort_values('data')
    )

    
    # --- Controle deslizante de datas no Streamlit ---
    data_min = df_agrupado['data'].min()
    data_max = df_agrupado['data'].max()

    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Volume total transacionado (Realizado) por URF
        </h2>
        """,
        unsafe_allow_html=True
    )

    intervalo_2 = st.slider(
        "Selecione o intervalo de datas:",
        min_value=data_min.to_pydatetime(),
        max_value=data_max.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max_slider.to_pydatetime()),
        format="YYYY-MM",
        key="slider_barras"
    )

    # Filtrar o DataFrame pelo intervalo selecionado
    mask_2 = (df_agrupado['data'] >= intervalo_2[0]) & (df_agrupado['data'] <= intervalo_2[1])
    df_intervalo_2 = df_agrupado.loc[mask_2].copy()

    # --- Criar o gráfico ---
    fig = go.Figure()

    for urf in urfs_grafico:
        dados_urf = df_intervalo_2[df_intervalo_2['urf'] == urf]
        fig.add_trace(
            go.Bar(
                x=dados_urf['data'],
                y=dados_urf['valor'],
                name=str(urf),
                hovertemplate="Data: %{x}<br>Volume: %{y:,.0f}<extra></extra>"
            )
        )

    # Layout geral (mesmo estilo visual anterior)
    fig.update_layout(
        barmode='group',  # barras lado a lado
        xaxis_title="Data",
        yaxis_title="Volume total",
        plot_bgcolor="#DBF7FF",
        paper_bgcolor="white",
        width=1200,
        height=600,
        legend_title="URF",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.25,  # legenda abaixo do gráfico
            xanchor="center",
            x=0.5,
            bgcolor="white",
            bordercolor="#042373",
            borderwidth=1
        ),
        margin=dict(t=100, b=120, l=50, r=50)
    )

    # Bordas e eixos
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linewidth=2,
        linecolor='#042373',
        mirror=True
    )

    fig.update_yaxes(
        showgrid=False,
        zeroline=False,
        showline=True,
        linewidth=2,
        linecolor='#042373',
        mirror=True
    )

    # Exibir no Streamlit
    st.plotly_chart(fig, use_container_width=True)


    import streamlit as st
    import plotly.graph_objects as go
    import pandas as pd
    import plotly.express as px

    # --- df1 já deve existir ---
    # df1 deve conter ['data', 'via', 'valor', 'Tipo']

    # Converter coluna 'data' para datetime
    df_2['data'] = pd.to_datetime(df_2['data'], errors='coerce')

    # Selecionar Vias que vamos mostrar (similar ao que fizemos com URFs)
    vias_grafico = df_2['via'].unique()

    # Filtrar dados apenas para essas Vias e Tipo = "Realizado"
    df_filtrado_volume = df_2[
        (df_2['via'].isin(vias_grafico)) &
        (df_2["coNcm"] == NCM)
    ]

    # Agrupar por data e via, somando o volume
    df_agrupado = (
        df_filtrado_volume
        .groupby(['data', 'via'], as_index=False)['metricStatistic']
        .sum()
        .sort_values('data')
    )

    # --- Slider de datas no Streamlit (key única) ---
    data_min = df_agrupado['data'].min()
    data_max = df_agrupado['data'].max()

    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Porcentagem por país de Volume (Realizado) Importado
        </h2>
        """,
        unsafe_allow_html=True
    )


    # Seleciona colunas relevantes
    df1_paises = df1_filtrado.loc[:, ['country', 'urf', 'metricStatistic', 'data']]
    df1_paises["metricStatistic"] = pd.to_numeric(df1_paises["metricStatistic"], errors='coerce')
    df1_paises["data"] = pd.to_datetime(df1_paises["data"], format="%Y-%m")

    # Slider de intervalo
    datas_disponiveis = sorted(df1_paises["data"].dt.strftime("%Y-%m").unique())

    intervalo = st.slider(
        "Selecione o intervalo de datas:",
        min_value=data_min.to_pydatetime(),
        max_value=data_max.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max_slider.to_pydatetime()),
        format="YYYY-MM",
        key="slider_volume_pais_agregado"
    )

    # Filtra pelo intervalo
    df1_paises_filtrado = df1_paises[
        (df1_paises["data"] >= intervalo[0]) &
        (df1_paises["data"] <= intervalo[1])
    ]

    # Ordena e reseta o índice
    df_grouped_paises = df1_paises_filtrado.sort_values("metricStatistic", ascending=False).reset_index(drop=True)

    # Calcula porcentagem acumulada
    df_grouped_paises["perc_acumulado"] = (
        df_grouped_paises["metricStatistic"].cumsum() /
        df_grouped_paises["metricStatistic"].sum()
    )

    # Encontra o índice do primeiro país que faz o acumulado ultrapassar 95%
    idx_limite = df_grouped_paises[df_grouped_paises["perc_acumulado"] >= 0.95].index.min()

    # Seleciona até esse índice (inclui o país que ultrapassa 95%)
    df_top95 = df_grouped_paises.iloc[:idx_limite + 1].copy()

    # Adiciona "Outros" se houver mais países depois do limite
    if idx_limite + 1 < len(df_grouped_paises):
        outros_valor = df_grouped_paises.iloc[idx_limite + 1:]["metricStatistic"].sum()
        if outros_valor > 0:
            df_outros = pd.DataFrame({
                "country": ["Outros"],
                "metricStatistic": [outros_valor]
            })
            df_top95 = pd.concat([df_top95, df_outros], ignore_index=True)

    # Gráfico de pizza
    fig_paises = px.pie(
        df_top95,
        names="country",
        values="metricStatistic",
        hole=0.3
    )

    # Exibe gráfico
    st.plotly_chart(fig_paises, use_container_width=True)



    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Porcentagem por país de Volume (Realizado) Importado Separado por URF
        </h2>
        """,
        unsafe_allow_html=True
    )
    import plotly.subplots as sp
    import plotly.graph_objects as go

    df1_paises["data"] = pd.to_datetime(df1_paises["data"], format="%Y-%m")

    intervalo_paises_sep = st.slider(
    "Selecione o intervalo de datas:",
    min_value=data_min.to_pydatetime(),
    max_value=data_max.to_pydatetime(),
    value=(data_default_inicio.to_pydatetime(), data_max.to_pydatetime()),
    format="YYYY-MM",
    key="slider_paises_sep"
    )

    #urfs_grafico = df1_paises['urf'].unique()

    cols_por_linha = 2  # número de colunas por linha
    linhas = -(-len(urfs_grafico) // cols_por_linha)

    # Filtra pelo intervalo de datas
    mask_paises_sep = (df1_paises['data'] >= intervalo_paises_sep[0]) & (df1_paises['data'] <= intervalo_paises_sep[1])
    dados_filtrados_paises_sep = df1_paises.loc[mask_paises_sep].copy()



    # Loop pelas URFs
    for i in range(0, len(urfs_grafico), cols_por_linha):
        cols = st.columns(cols_por_linha)
        for j, urf in enumerate(urfs_grafico[i:i+cols_por_linha]):
            with cols[j]:
                # Filtra dados da URF
                dados_urf = dados_filtrados_paises_sep[dados_filtrados_paises_sep["urf"] == urf]
                df_grouped = dados_urf.groupby("country", as_index=False)["metricStatistic"].sum()
                df_grouped_paises = df_grouped.sort_values("metricStatistic", ascending=False).reset_index(drop=True)

                # Calcula porcentagem acumulada
                df_grouped_paises["perc_acumulado"] = (
                    df_grouped_paises["metricStatistic"].cumsum() /
                    df_grouped_paises["metricStatistic"].sum()
                )

                # Encontra o índice que faz ultrapassar 95%
                if not df_grouped_paises.empty:
                    idx_limite = df_grouped_paises[df_grouped_paises["perc_acumulado"] >= 0.95].index.min()

                    # Seleciona até o país que faz ultrapassar 95%
                    df_top95 = df_grouped_paises.iloc[:idx_limite + 1].copy()

                    # Soma os demais como "Outros"
                    if idx_limite + 1 < len(df_grouped_paises):
                        outros_valor = df_grouped_paises.iloc[idx_limite + 1:]["metricStatistic"].sum()
                        if outros_valor > 0:
                            df_outros = pd.DataFrame({
                                "country": ["Outros"],
                                "metricStatistic": [outros_valor]
                            })
                            df_top95 = pd.concat([df_top95, df_outros], ignore_index=True)

                    # Gráfico de pizza para esta URF
                    fig_pizza = go.Figure(go.Pie(
                        labels=df_top95["country"],
                        values=df_top95["metricStatistic"],
                        hole=0.3,
                        textinfo="percent+label",
                        hovertemplate="%{label}: %{percent:.1%}<extra></extra>"
                    ))

                    fig_pizza.update_layout(
                        title={
                            "text": f"URF: {urf}",
                            "font": {
                                "color": "#042373",   # 💙 cor personalizada (mude aqui)
                                "size": 14,           # tamanho da fonte
                                "family": "Arial, bold"  # fonte e estilo
                            }
                        },
                        height=300,
                        margin=dict(t=40, b=0, l=0, r=0)
                    )

                    st.plotly_chart(fig_pizza, use_container_width=True)






    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Volume total transacionado (Realizado) por Via
        </h2>
        """,
        unsafe_allow_html=True
    )

    intervalo_via = st.slider(
        "Selecione o intervalo de datas:",
        min_value=data_min.to_pydatetime(),
        max_value=data_max.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max_slider.to_pydatetime()),
        format="YYYY-MM",
        key="slider_volume_via_1"  # chave única
    )

    # Filtrar pelo intervalo selecionado
    mask = (df_agrupado['data'] >= intervalo_via[0]) & (df_agrupado['data'] <= intervalo_via[1])
    df_intervalo = df_agrupado.loc[mask].copy()

    # --- Criar gráfico de barras ---
    fig = go.Figure()

    for via in vias_grafico:
        dados_via = df_intervalo[df_intervalo['via'] == via]
        fig.add_trace(
            go.Bar(
                x=dados_via['data'],
                y=dados_via['metricStatistic'],
                name=str(via),
                hovertemplate="Data: %{x}<br>Volume: %{y:,.0f}<extra></extra>"
            )
        )

    # Layout geral
    fig.update_layout(
        barmode='group',  # barras lado a lado
        xaxis_title="Data",
        yaxis_title="Volume total",
        plot_bgcolor="#DBF7FF",
        paper_bgcolor="white",
        width=1200,
        height=600,
        legend_title="Via",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.25,  # legenda abaixo do gráfico
            xanchor="center",
            x=0.5,
            bgcolor="white",
            bordercolor="#042373",
            borderwidth=1
        ),
        margin=dict(t=100, b=120, l=50, r=50)
    )

    # Bordas e eixos
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linewidth=2,
        linecolor='#042373',
        mirror=True
    )

    fig.update_yaxes(
        showgrid=False,
        zeroline=False,
        showline=True,
        linewidth=2,
        linecolor='#042373',
        mirror=True
    )

    # Exibir no Streamlit
    st.plotly_chart(fig, use_container_width=True)


    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Volume total transacionado (Realizado) por País
        </h2>
        """,
        unsafe_allow_html=True
    )
    # Converter coluna 'data' para datetime (caso ainda não esteja)
    df1_paises['data'] = pd.to_datetime(df1_paises['data'], errors='coerce')


    # --- Agrupar por país, somando o volume total ---
    df_grouped_paises = (
        df1_paises_filtrado
        .groupby("country", as_index=False)["metricStatistic"]
        .sum()
        .sort_values("metricStatistic", ascending=False)
        .reset_index(drop=True)
    )

    # --- Calcular porcentagem acumulada ---
    df_grouped_paises["perc_acumulado"] = (
        df_grouped_paises["metricStatistic"].cumsum() /
        df_grouped_paises["metricStatistic"].sum()
    )

    # --- Encontrar o índice do país que faz o acumulado ultrapassar 95% ---
    idx_candidates = df_grouped_paises[df_grouped_paises["perc_acumulado"] >= 0.95].index

    # ✅ Tratamento robusto para evitar erro se não houver país acima de 95%
    if len(idx_candidates) == 0:
        idx_limite = len(df_grouped_paises) - 1  # usa o último país
    else:
        idx_limite = idx_candidates.min()

    # --- Selecionar até esse índice (inclui o país que ultrapassa 95%) ---
    df_top95 = df_grouped_paises.iloc[:idx_limite + 1].copy()

    # --- Adicionar "Outros" se houver países além do limite ---
    if idx_limite + 1 < len(df_grouped_paises):
        outros_valor = df_grouped_paises.iloc[idx_limite + 1:]["metricStatistic"].sum()
        if outros_valor > 0:
            df_outros = pd.DataFrame({
                "country": ["Outros"],
                "metricStatistic": [outros_valor]
            })
            df_top95 = pd.concat([df_top95, df_outros], ignore_index=True)

    # --- Ordenar novamente para visualização ---
    df_top95 = df_top95.sort_values("metricStatistic", ascending=False)

    # --- Criar gráfico de barras ---
    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=df_top95["country"],
            y=df_top95["metricStatistic"],
            text=df_top95["metricStatistic"],
            texttemplate="%{text:,.0f}",
            textposition='outside',
            marker_color="#042373",
            hovertemplate="País: %{x}<br>Volume: %{y:,.0f}<extra></extra>"
        )
    )

    # --- Layout do gráfico ---
    fig.update_layout(
        title="Volume total por País (Top 95% + Outros)",
        xaxis_title="País",
        yaxis_title="Volume total (metricStatistic)",
        plot_bgcolor="#DBF7FF",
        paper_bgcolor="white",
        width=1000,
        height=600,
        showlegend=False,
        margin=dict(t=100, b=100, l=80, r=40),
    )

    # --- Ajuste de eixos ---
    fig.update_xaxes(
        showline=True,
        linewidth=2,
        linecolor='#042373',
        tickangle=-45,
        mirror=True
    )
    fig.update_yaxes(
        showline=True,
        linewidth=2,
        linecolor='#042373',
        gridcolor='rgba(4,35,115,0.1)',
        mirror=True
    )

    # --- Exibir no Streamlit ---
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        """
        <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
            Volume total transacionado (Realizado) por País separado por URF
        </h2>
        """,
        unsafe_allow_html=True
    )

    # Converter coluna de data para datetime
    df1_paises["data"] = pd.to_datetime(df1_paises["data"], format="%Y-%m")

    # --- Slider de intervalo de datas ---
    intervalo_paises_sep = st.slider(
        "Selecione o intervalo de datas:",
        min_value=data_min.to_pydatetime(),
        max_value=data_max.to_pydatetime(),
        value=(data_default_inicio.to_pydatetime(), data_max.to_pydatetime()),
        format="YYYY-MM",
        key="slider_paises_sep_urf"
    )

    # --- Filtrar dados pelo intervalo de datas ---
    mask_paises_sep = (df1_paises['data'] >= intervalo_paises_sep[0]) & (df1_paises['data'] <= intervalo_paises_sep[1])
    dados_filtrados_paises_sep = df1_paises.loc[mask_paises_sep].copy()


    # --- Configuração do grid ---
    cols_por_linha = 2
    linhas = -(-len(urfs_grafico) // cols_por_linha)

    # --- Loop pelas URFs ---
    for i in range(0, len(urfs_grafico), cols_por_linha):
        cols = st.columns(cols_por_linha)
        for j, urf in enumerate(urfs_grafico[i:i + cols_por_linha]):
            with cols[j]:
                # --- Filtrar dados da URF ---
                dados_urf = dados_filtrados_paises_sep[dados_filtrados_paises_sep["urf"] == urf]

                # --- Se URF não tiver dados, pula ---
                if dados_urf.empty:
                    continue

                # --- Agrupar por país e somar volumes ---
                df_grouped = (
                    dados_urf.groupby("country", as_index=False)["metricStatistic"]
                    .sum()
                    .sort_values("metricStatistic", ascending=False)
                    .reset_index(drop=True)
                )

                # --- Calcular porcentagem acumulada ---
                df_grouped["perc_acumulado"] = (
                    df_grouped["metricStatistic"].cumsum() / df_grouped["metricStatistic"].sum()
                )

                # --- Encontrar índice limite dos 95% (com tratamento seguro) ---
                idx_candidates = df_grouped[df_grouped["perc_acumulado"] >= 0.95].index
                if len(idx_candidates) == 0:
                    idx_limite = len(df_grouped) - 1
                else:
                    idx_limite = idx_candidates.min()

                # --- Selecionar até o país que faz ultrapassar 95% ---
                df_top95 = df_grouped.iloc[:idx_limite + 1].copy()

                # --- Adicionar “Outros” se houver mais países ---
                if idx_limite + 1 < len(df_grouped):
                    outros_valor = df_grouped.iloc[idx_limite + 1:]["metricStatistic"].sum()
                    if outros_valor > 0:
                        df_outros = pd.DataFrame({
                            "country": ["Outros"],
                            "metricStatistic": [outros_valor]
                        })
                        df_top95 = pd.concat([df_top95, df_outros], ignore_index=True)

                # --- Ordenar para exibição ---
                df_top95 = df_top95.sort_values("metricStatistic", ascending=False)

                # --- Criar gráfico de barras ---
                fig_bar = go.Figure()

                fig_bar.add_trace(
                    go.Bar(
                        x=df_top95["country"],
                        y=df_top95["metricStatistic"],
                        text=df_top95["metricStatistic"],
                        texttemplate="%{text:,.0f}",
                        textposition='outside',
                        marker_color="#042373",
                        hovertemplate="País: %{x}<br>Volume: %{y:,.0f}<extra></extra>"
                    )
                )

                # --- Layout do gráfico ---
                fig_bar.update_layout(
                    title={
                        "text": f"URF: {urf}",
                        "font": {
                            "color": "#042373",
                            "size": 14,
                            "family": "Arial, bold"
                        }
                    },
                    xaxis_title="País",
                    yaxis_title="Volume total (metricStatistic)",
                    plot_bgcolor="#DBF7FF",
                    paper_bgcolor="white",
                    showlegend=False,
                    height=400,
                    margin=dict(t=60, b=80, l=60, r=40)
                )

                # --- Eixos ---
                fig_bar.update_xaxes(
                    showline=True,
                    linewidth=2,
                    linecolor='#042373',
                    tickangle=-45,
                    mirror=True
                )
                fig_bar.update_yaxes(
                    showline=True,
                    linewidth=2,
                    linecolor='#042373',
                    gridcolor='rgba(4,35,115,0.1)',
                    mirror=True
                )

                # --- Exibir gráfico ---
                st.plotly_chart(fig_bar, use_container_width=True)


else:
    df = pd.DataFrame()
