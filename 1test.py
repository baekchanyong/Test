import streamlit as st
import pandas as pd
import requests
import io
import time
from bs4 import BeautifulSoup

# 페이지 기본 설정
st.set_page_config(page_title="주식탐색기 Ver 1.2", page_icon="📈", layout="wide")

# --- 헤더 ---
st.title("주식탐색기 Ver 1.2")
with st.expander("📝 패치노트 (클릭하여 열기)"):
    st.markdown("""
    **✅ (26.04.15) Ver 1.0**
    - 주식탐색기 최초 배포
    
    **✅ (26.04.16) Ver 1.1**
    - KOSDAQ 종목 탐색 기능 추가
    - 전화 수신, 인터넷 창 닫힘 등 백그라운드 전환 시 Data 일시저장 기능 추가
    - 기존 탐색 결과에서 이어서 탐색 및 추가 탐색 기능 지원
    
    **✅ (26.04.17) Ver 1.2**
    - 탐색필터 및 과거 적정주가, 목표주가 도출 로직 추가
    - 탐색결과표 시각적 디자인 개편 및 UI 편의성 개선
    """)

# --- 계산식 안내 ---
st.markdown("### 🧮 산출 방식 안내")
st.markdown("""
- **적정주가**: `(연간 EPS * 10 or 15) + BPS - 부채 패널티`
  - 🔻 *부채 패널티 산식* (부채비율 기준)
    - **100% 이하**: `0` (패널티 없음)
    - **100% 초과**: `(총부채 - 총자본) / 상장주식수`
- **목표주가**: `(연간 예상 EPS * 10 or 15) + BPS - (유동부채 / 주식수)`
- **데이터 출처**: 연간 EPS(최신 실적/예상)
- **정렬**: 괴리율(10) 낮은 순 (저평가 매력 순)
""")

st.divider()

# --- 상태 관리 초기화 ---
if 'running' not in st.session_state: st.session_state.running = False
if 'results' not in st.session_state: st.session_state.results = []
if 'skipped_results' not in st.session_state: st.session_state.skipped_results = []
if 'market_df' not in st.session_state: st.session_state.market_df = pd.DataFrame()
if 'current_idx' not in st.session_state: st.session_state.current_idx = 0
if 'target_stocks' not in st.session_state: st.session_state.target_stocks = []

# --- 시장(KOSPI/KOSDAQ) 정보 로드 함수 ---
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_page_data(sosok, page):
    url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        table = soup.find('table', {'class': 'type_2'})
        if not table: return [], False
        
        data = []
        has_data = False
        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) > 5:
                a_tag = cols[1].find('a')
                if a_tag:
                    name = a_tag.text.strip()
                    code = a_tag['href'].split('code=')[-1]
                    close_txt = cols[2].text.strip().replace(',', '')
                    stocks_txt = cols[7].text.strip().replace(',', '')
                    if close_txt and stocks_txt:
                        data.append({
                            'Code': code, 'Name': name,
                            'Close': float(close_txt),
                            'Stocks': float(stocks_txt) * 1000
                        })
                        has_data = True
        return data, has_data
    except: return [], False

if st.session_state.market_df.empty:
    loading_placeholder = st.empty()
    progress_bar = st.empty()
    
    data = []
    start_time = time.time()
    
    total_est = 55
    current_idx = 0
    for sosok, market_name in [(0, 'KOSPI'), (1, 'KOSDAQ')]:
        marcap_rank = 1
        for page in range(1, 45):
            current_idx += 1
            percent = min(current_idx / total_est, 1.0)
            elapsed = time.time() - start_time
            if current_idx > 1:
                eta = elapsed / current_idx * (total_est - current_idx)
            else:
                eta = 0
                
            eta_val = max(0, int(eta))
            if eta_val > 0:
                loading_placeholder.markdown(f"### ⏳ Data Loading 중... (예상 남은 시간: {eta_val}초)")
            else:
                loading_placeholder.markdown("### ⏳ Data 최종 정리중...")
            progress_bar.progress(percent)
            
            page_data, has_data = fetch_page_data(sosok, page)
            if not has_data:
                break
            
            for item in page_data:
                item['Market'] = market_name
                item['Marcap_Rank'] = marcap_rank
                data.append(item)
                marcap_rank += 1
                
    st.session_state.market_df = pd.DataFrame(data)
    loading_placeholder.empty()
    progress_bar.empty()
    st.rerun()

# --- 스크래핑 및 계산 함수 ---
def safe_float(val):
    try:
        if pd.isna(val): return None
        v = str(val).replace(',', '').strip()
        if not v or v == '-' or v == 'N/A': return None
        return float(v)
    except:
        return None

def analyze_stock(ticker, name, market, current_price, shares, marcap_rank, filters):
    is_pref = not str(ticker).endswith('0') or name.endswith('우') or '우(' in name or '우B' in name
    if is_pref and filters.get('pref', True):
        return None, {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "제외사유": "우선주 제외"}
    
    is_etf = any(k in name for k in ['KODEX', 'TIGER', 'KBSTAR', 'KINDEX', 'ARIRANG', 'KOSEF', 'HANARO', 'ACE', 'SOL', 'TIMEFOLIO', 'FOCUS', '마이티', 'TREX', '히어로즈', 'VITA']) or name.endswith('ETF')
    if filters.get('etf', True) and is_etf:
        return None, {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "제외사유": "ETF 제외"}
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    url_fin = f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{ticker}"
    url_main = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{ticker}"
    
    t_equity = 0.0; t_debt = 0.0; c_liab = 0.0; a_eps = 0.0; past_eps = 0.0; q_eps = 0.0; bps = 0.0; past_bps = 0.0; op_profit = 0.0; is_future_eps = False
    try:
        res_fin = requests.get(url_fin, headers=headers, timeout=5)
        res_fin.encoding = 'utf-8'
        tables_fin = pd.read_html(io.StringIO(res_fin.text))
        df_bs = tables_fin[2]
        for _, row in df_bs.iterrows():
            nm = str(row.iloc[0]).replace('\xa0', ' ').replace(' ', '').strip()
            if nm in ['자본', '자본총계'] and t_equity == 0: 
                for v in row.values[::-1]:
                    vf = safe_float(v)
                    if vf is not None: t_equity = vf * 100000000; break
            if nm in ['부채', '부채총계'] and t_debt == 0: 
                for v in row.values[::-1]:
                    vf = safe_float(v)
                    if vf is not None: t_debt = vf * 100000000; break
            if '유동부채' in nm and '비유동' not in nm and c_liab == 0: 
                for v in row.values[::-1]:
                    vf = safe_float(v)
                    if vf is not None: c_liab = vf * 100000000; break
    except: return None, {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "제외사유": "재무 데이터 로드 오류"}
        
    try:
        res_main = requests.get(url_main, headers=headers, timeout=5)
        res_main.encoding = 'utf-8'
        tables_main = pd.read_html(io.StringIO(res_main.text))
        for df in tables_main:
            if df.columns.nlevels > 1:
                col_types = [str(c[0]).strip() for c in df.columns]
                col_names = [str(c[1]).strip() for c in df.columns]
                for _, row in df.iterrows():
                    nm = str(row.iloc[0]).replace('\xa0', ' ').replace(' ', '').strip()
                    if 'EPS(원)' in nm:
                        annuals = []
                        for j in range(len(row)-1, 0, -1):
                            val = safe_float(row.iloc[j])
                            if val is not None:
                                if 'Annual' in col_types[j]:
                                    annuals.append((val, '(E)' in col_names[j]))
                                elif 'NetQuarter' in col_types[j].replace(' ', '') and q_eps == 0:
                                    q_eps = val
                        if annuals:
                            a_eps = annuals[0][0]
                            is_future_eps = annuals[0][1]
                            if len(annuals) > 1: past_eps = annuals[1][0]
                            else: past_eps = a_eps
                    elif 'BPS(원)' in nm and bps == 0:
                        bps_annuals = []
                        for j in range(len(row)-1, 0, -1):
                            val = safe_float(row.iloc[j])
                            if val is not None:
                                if 'Annual' in col_types[j]:
                                    bps_annuals.append(val)
                                if bps == 0 and ('Annual' in col_types[j] or 'NetQuarter' in col_types[j].replace(' ', '')):
                                    bps = val
                        if bps_annuals:
                            if len(bps_annuals) > 1: past_bps = bps_annuals[1]
                            else: past_bps = bps_annuals[0]
                    elif '영업이익' in nm and op_profit == 0:
                        for j in range(len(row)-1, 0, -1):
                            val = safe_float(row.iloc[j])
                            if val is not None and 'Annual' in col_types[j]:
                                op_profit = val
                                break
            if a_eps != 0 and q_eps != 0 and bps != 0: break
    except: return None, {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "제외사유": "재무 데이터 파싱 오류"}

    if a_eps == 0 or bps == 0 or t_equity == 0: return None, {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "제외사유": "필요 데이터 누락"}
    if q_eps == 0: q_eps = a_eps / 4

    d_ratio = (t_debt / t_equity) * 100
    pnlty = ((t_debt - t_equity) / shares) if d_ratio > 100 else 0
    p10 = (a_eps * 10) + bps - pnlty
    p15 = (a_eps * 15) + bps - pnlty
    t10 = (a_eps * 10) + bps - (c_liab / shares)
    t15 = (a_eps * 15) + bps - (c_liab / shares)
    
    past_p10 = (past_eps * 10) + past_bps - pnlty
    past_t10 = (past_eps * 10) + past_bps - (c_liab / shares)
    past_t15 = (past_eps * 15) + past_bps - (c_liab / shares)
    
    reason = None
    if a_eps <= 0 and filters.get('eps_neg', True): reason = "적자 기업 (EPS < 0)"
    elif t10 <= 0 and filters.get('target_neg', True): reason = "목표주가 음수"
    elif p10 <= 0 and filters.get('intrinsic_neg', True): reason = "적정주가 음수"
    elif (a_eps * 10) < bps and filters.get('eps10_bps', True): reason = "EPS*10 < BPS"
    elif op_profit < 0 and filters.get('op_neg', True): reason = "영업이익 적자"
    
    data = {"시장": market, "시총순위": marcap_rank, "종목명": name, "현재주가": current_price, "적정주가(10)": p10, "목표주가(10)": t10, "괴리율(10)": ((p10 - current_price) / current_price) * 100 if p10 > 0 else 0, "적정주가(15)": p15, "목표주가(15)": t15, "EPS": a_eps, "추정EPS여부": is_future_eps, "BPS": bps, "과거적정주가": past_p10, "과거목표주가(10)": past_t10, "과거목표주가(15)": past_t15, "과거EPS": past_eps, "과거BPS": past_bps, "부채비율(%)": d_ratio, "총부채_원": t_debt, "유동부채_원": c_liab, "총자본_원": t_equity, "상장주식수_원": shares}
    if reason:
        data["제외사유"] = reason
        return (None, data)
    return (data, None)

# --- UI 설정 ---
market_df = st.session_state.market_df
if not market_df.empty:
    st.markdown("### ⚙️ 탐색 모드 설정")
    top_n = 50
    selected_custom = []
    col1, col2 = st.columns([1, 2])
    with col1:
        search_mode = st.radio("모드를 선택하세요:", ("KOSPI 전체 탐색", "KOSPI 상위 N개 탐색", "KOSDAQ 전체 탐색", "KOSDAQ 상위 N개 탐색", "사용자 지정 탐색"))
    with col2:
        if "상위 N개" in search_mode:
            cur_market = "KOSPI" if "KOSPI" in search_mode else "KOSDAQ"
            max_len = len(market_df[market_df['Market'] == cur_market])
            top_n = st.number_input("탐색 개수:", min_value=1, max_value=max_len if max_len > 0 else 1, value=50 if max_len >= 50 else max_len)
        elif search_mode == "사용자 지정 탐색":
            selected_custom = st.multiselect("종목 선택:", market_df['Name'].tolist())

    st.markdown("### 🔍 탐색 필터 (체크 시 분석에서 제외)")
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1: st.checkbox("우선주", value=True, key="filter_pref")
    with col_f2: st.checkbox("ETF 종목", value=True, key="filter_etf")
    with col_f3: st.checkbox("적자기업 (EPS 음수)", value=True, key="filter_eps_neg")
    with col_f4: st.checkbox("영업이익 적자", value=True, key="filter_op_neg")
    
    col_f5, col_f6, col_f7, _ = st.columns(4)
    with col_f5: st.checkbox("목표주가 음수", value=True, key="filter_target_neg")
    with col_f6: st.checkbox("적정주가 음수", value=True, key="filter_intrinsic_neg")
    with col_f7: st.checkbox("EPS*10 < BPS", value=True, key="filter_eps10_bps")

    st.divider()
    
    def get_targets():
        if search_mode == "KOSPI 전체 탐색": return market_df[market_df['Market'] == 'KOSPI'].to_dict('records')
        elif search_mode == "KOSDAQ 전체 탐색": return market_df[market_df['Market'] == 'KOSDAQ'].to_dict('records')
        elif search_mode == "KOSPI 상위 N개 탐색": return market_df[market_df['Market'] == 'KOSPI'].head(top_n).to_dict('records')
        elif search_mode == "KOSDAQ 상위 N개 탐색": return market_df[market_df['Market'] == 'KOSDAQ'].head(top_n).to_dict('records')
        else: return market_df[market_df['Name'].isin(selected_custom)].to_dict('records')

    btn_col1, btn_col2, btn_col3, _ = st.columns([1, 1.2, 1, 1.8])
    with btn_col1:
        if st.button("🚀 새로 탐색", disabled=st.session_state.running):
            st.session_state.target_stocks = get_targets()
            st.session_state.results = []; st.session_state.skipped_results = []; st.session_state.current_idx = 0
            if len(st.session_state.target_stocks) > 0: st.session_state.running = True
            st.rerun()
    with btn_col2:
        if st.button("▶️ 이어서/추가 탐색", disabled=st.session_state.running):
            new_targets = get_targets()
            
            existing_codes = set([s['Code'] for s in st.session_state.target_stocks])
            for stock in new_targets:
                if stock['Code'] not in existing_codes:
                    st.session_state.target_stocks.append(stock)
            
            if st.session_state.current_idx < len(st.session_state.target_stocks):
                st.session_state.running = True
            st.rerun()
    with btn_col3:
        if st.button("⏹️ 일시정지", disabled=not st.session_state.running):
            st.session_state.running = False; st.rerun()

    progress_container = st.empty(); status_text = st.empty()

    def render_result_table():
        def fmt_curr(v): return f"{v/1e8:,.1f}"
        if len(st.session_state.results) > 0:
            st.markdown("### 🏆 탐색 결과")
            st.caption("""ℹ️ **주가 산출 시 데이터 기준 및 색상 안내**
- **기본/검정색**: 현재 존재하는 실제 실적으로 산출됨.
- **<span style='color:#cda8ff; font-weight:bold;'>보라색 표기</span>**: 예측치(미래 추정치)로 산출됨. 
- **EPS 적용 방식**: 1순위로 미래 예측치(E)를 우선 반영하며, 예측치가 없을 경우에만 가장 최근 발표된 실제 연간 EPS를 사용합니다.
- **BPS 적용 방식**: 예측치 여부와 무관하게, 가장 최근 발표된 연간 또는 최신 분기(NetQuarter) 실적 중 가장 최신의 값을 현재 BPS로 반영합니다.""", unsafe_allow_html=True)
            df = pd.DataFrame(st.session_state.results).sort_values("괴리율(10)", ascending=False).reset_index(drop=True)
            res = pd.DataFrame()
            res["시장"] = df.get("시장", "KOSPI"); res["순위"] = df.index+1; res["종목"] = df["종목명"]; res["시총순위"] = df["시총순위"]; res["현재주가(원)"] = df["현재주가"].apply(lambda x: f"{x:,.0f}")
            res["적정주가(10, 원)"] = df["적정주가(10)"].apply(lambda x: f"{x:,.0f}"); res["목표주가(10, 원)"] = df["목표주가(10)"].apply(lambda x: f"{x:,.0f}")
            res["괴리율(10, %)"] = df["괴리율(10)"].apply(lambda x: f"{x:.2f}"); res["적정주가(15, 원)"] = df["적정주가(15)"].apply(lambda x: f"{x:,.0f}"); res["목표주가(15, 원)"] = df["목표주가(15)"].apply(lambda x: f"{x:,.0f}")
            res["EPS(원)"] = df["EPS"].apply(lambda x: f"{x:,.0f}"); res["BPS(원)"] = df["BPS"].apply(lambda x: f"{x:,.0f}")
            res["과거 적정주가"] = df["과거적정주가"].apply(lambda x: f"{x:,.0f}"); res["과거목표주가(10)"] = df["과거목표주가(10)"].apply(lambda x: f"{x:,.0f}"); res["과거목표주가(15)"] = df["과거목표주가(15)"].apply(lambda x: f"{x:,.0f}")
            res["과거 EPS(원)"] = df.get("과거EPS", 0).apply(lambda x: f"{x:,.0f}"); res["과거 BPS(원)"] = df.get("과거BPS", 0).apply(lambda x: f"{x:,.0f}")
            res["부채비율(%)"] = df["부채비율(%)"].apply(lambda x: f"{x:.2f}")
            res["총부채(억원)"] = df["총부채_원"].apply(fmt_curr); res["유동부채(억원)"] = df["유동부채_원"].apply(fmt_curr); res["총자본(억원)"] = df["총자본_원"].apply(fmt_curr); res["주식수(만개)"] = df["상장주식수_원"].apply(lambda x: f"{x/1e4:,.0f}")

            # 가로 횡스크롤 시 좌측에 고정되도록 인덱스 설정
            res = res.set_index(["시장", "순위", "종목"])

            def highlight_eps_cols(row):
                styles = [''] * len(row)
                original_idx = int(row.name[1]) - 1  # 다중 인덱스의 '순위' 값으로 원래 인덱스 추적
                
                # 행 짝수/홀수 구분 (스트라이프 배경으로 주식별 구분)
                is_even = (original_idx % 2 == 0)
                base_bg = 'background-color: rgba(128, 128, 128, 0.05); ' if is_even else 'background-color: transparent; '
                
                # 데이터 그룹별 배경색 (현재 데이터=옅은 파랑톤, 과거 데이터=옅은 오렌지/갈색톤)
                group1_bg = 'background-color: rgba(60, 130, 250, 0.12); ' if is_even else 'background-color: rgba(60, 130, 250, 0.05); '
                group2_bg = 'background-color: rgba(250, 130, 60, 0.12); ' if is_even else 'background-color: rgba(250, 130, 60, 0.05); '
                
                group1_cols = ['적정주가(10, 원)', '목표주가(10, 원)', '괴리율(10, %)', '적정주가(15, 원)', '목표주가(15, 원)', 'EPS(원)', 'BPS(원)']
                group2_cols = ['과거 적정주가', '과거목표주가(10)', '과거목표주가(15)', '과거 EPS(원)', '과거 BPS(원)']
                
                text_color = ''
                if df.get("추정EPS여부", pd.Series([False]*len(df))).iloc[original_idx]:
                    text_color = 'color: #cda8ff; font-weight: bold; '
                
                for i, col_name in enumerate(row.index):
                    cell_style = base_bg
                    
                    if col_name in group1_cols:
                        cell_style = group1_bg
                        if col_name == '적정주가(10, 원)': cell_style += 'border-left: 2px solid rgba(128,128,128,0.2); '
                        if col_name == 'BPS(원)': cell_style += 'border-right: 2px solid rgba(128,128,128,0.2); '
                    elif col_name in group2_cols:
                        cell_style = group2_bg
                        if col_name == '과거 적정주가': cell_style += 'border-left: 2px solid rgba(128,128,128,0.2); '
                        if col_name == '과거 BPS(원)': cell_style += 'border-right: 2px solid rgba(128,128,128,0.2); '
                    
                    if col_name in ['적정주가(10, 원)', '목표주가(10, 원)', '적정주가(15, 원)', '목표주가(15, 원)', 'EPS(원)'] and text_color != '':
                        cell_style += text_color
                        
                    styles[i] = cell_style
                    
                return styles
            
            styled_res = res.style.apply(highlight_eps_cols, axis=1)
            st.dataframe(styled_res, use_container_width=True) # 고정된 인덱스를 보여주기 위해 hide_index=True 제거
        if len(st.session_state.skipped_results) > 0:
            with st.expander("🚫 분석 제외 종목", expanded=True):
                dfS = pd.DataFrame(st.session_state.skipped_results).sort_values("시총순위")
                skip = pd.DataFrame()
                skip["시장"] = dfS.get("시장", "KOSPI"); skip["종목"] = dfS["종목명"]; skip["시총순위"] = dfS["시총순위"]; skip["사유"] = dfS.get("제외사유", "데이터 오류"); skip["현재주가(원)"] = dfS["현재주가"].apply(lambda x: f"{float(x):,.0f}")
                st.dataframe(skip, use_container_width=True, hide_index=True)

    if st.session_state.running:
        total = len(st.session_state.target_stocks)
        prog = progress_container.progress(0.0)
        for i, stock in enumerate(st.session_state.target_stocks[st.session_state.current_idx:], start=st.session_state.current_idx):
            prog.progress((i)/total); status_text.markdown(f"**진행중:** {i}/{total} ({stock['Name']})")
            filters = {
                'pref': st.session_state.get('filter_pref', True),
                'target_neg': st.session_state.get('filter_target_neg', True),
                'intrinsic_neg': st.session_state.get('filter_intrinsic_neg', True),
                'eps_neg': st.session_state.get('filter_eps_neg', True),
                'eps10_bps': st.session_state.get('filter_eps10_bps', True),
                'op_neg': st.session_state.get('filter_op_neg', True),
                'etf': st.session_state.get('filter_etf', True)
            }
            res, extra = analyze_stock(stock['Code'], stock['Name'], stock.get('Market', 'KOSPI'), float(stock['Close']), float(stock['Stocks']), stock['Marcap_Rank'], filters)
            if res: st.session_state.results.append(res)
            else: st.session_state.skipped_results.append(extra)
            render_result_table(); st.session_state.current_idx = i + 1; time.sleep(0.3)
        st.session_state.running = False; progress_container.empty(); status_text.success("완료!"); st.rerun()
    else: render_result_table()
