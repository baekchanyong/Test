import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import time
import requests
import re
from datetime import datetime, timedelta
import concurrent.futures

# --- [비밀번호 설정] ---
my_password = "1414"

st.set_page_config(page_title="KOSPI 분석기 2.0", page_icon="🎨", layout="wide")

# 비밀번호 입력 (사이드바가 아닌 메인에 배치하여 깔끔하게)
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pw = st.text_input("🔒 비밀번호를 입력하세요", type="password")
    if pw:
        if pw == my_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

# --- [CSS] 스타일링 ---
st.markdown("""
<style>
    /* 전체 폰트 및 배경 */
    .stApp {
        font-family: 'Pretendard', sans-serif;
    }
    
    /* 헤더 스타일 */
    .main-header {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(to right, #6a11cb 0%, #2575fc 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    
    /* 정보 박스 스타일 */
    .info-box {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #5C7CFA;
        margin-bottom: 20px;
    }
    
    /* 파스텔톤 텍스트 */
    .pastel-blue { color: #5C7CFA; font-weight: bold; }
    .pastel-red { color: #D47C94; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- 헬퍼 함수 ---
def to_float(val):
    try:
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        clean_val = re.sub(r'[(),%]', '', str(val))
        return float(clean_val)
    except: return 0.0

# --- 데이터 로딩 (캐싱) ---
@st.cache_data
def get_stock_listing():
    df = fdr.StockListing('KOSPI')
    if 'Symbol' in df.columns: df = df.rename(columns={'Symbol': 'Code'})
    
    if 'Close' in df.columns:
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    if 'Marcap' in df.columns:
        df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)

    if 'Marcap' in df.columns:
        df = df.sort_values(by='Marcap', ascending=False)
        df['ActualRank'] = range(1, len(df) + 1)
        df['Shares'] = np.where(df['Close'] > 0, df['Marcap'] / df['Close'], 0)
    else:
        df['ActualRank'] = 0
        df['Shares'] = 0
    return df

# --- 적정주가 산출 (부채 반영) ---
def calculate_fair_value_v2(eps, bps, debt_total, equity_total, shares):
    if shares <= 0: return 0
    base_price = (eps * 10) + bps
    
    if equity_total > 0:
        debt_ratio = (debt_total / equity_total) * 100
        if debt_ratio > 100:
            excess_debt = (debt_total - equity_total) * 100000000
            penalty = excess_debt / shares
            return base_price - penalty
    return base_price

# --- 크롤링 ---
def fetch_stock_data(item):
    code, name, rank, shares = item
    current_price = 0.0
    prev_eps, prev_bps, prev_debt, prev_equity = 0.0, 0.0, 0.0, 0.0
    target_eps, target_bps, target_debt, target_equity = 0.0, 0.0, 0.0, 0.0
    quarter_debt, quarter_equity = 0.0, 0.0
    
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        
        try:
             match = re.search(r'blind">\s*([0-9,]+)\s*<', res.text)
             if match: current_price = to_float(match.group(1))
        except: pass

        dfs = pd.read_html(res.text, encoding='cp949')
        
        for df in dfs:
            if '매출액' in df.iloc[:, 0].to_string() or '영업이익' in df.iloc[:, 0].to_string():
                df = df.set_index(df.columns[0])
                if isinstance(df.columns, pd.MultiIndex): cols = [str(c[1]) for c in df.columns]
                else: cols = [str(c) for c in df.columns]
                
                est_idx = -1
                for i, c in enumerate(cols):
                    if '(E)' in c and re.search(r'\d{4}\.\d{2}', c):
                        est_idx = i
                        break
                
                prev_idx = est_idx - 1 if est_idx != -1 else (3 if len(cols) > 3 else -1)
                quarter_idx = len(cols) - 1

                def get_data(row_name, col_idx):
                    if col_idx < 0: return 0.0
                    try:
                        target_rows = df.index[df.index.str.contains(row_name, na=False)]
                        if len(target_rows) > 0:
                            return to_float(df.iloc[df.index.get_loc(target_rows[0]), col_idx])
                    except: pass
                    return 0.0

                prev_eps = get_data('EPS', prev_idx)
                prev_bps = get_data('BPS', prev_idx)
                prev_debt = get_data('부채총계', prev_idx)
                prev_equity = get_data('자본총계', prev_idx)
                
                target_idx = est_idx if est_idx != -1 else prev_idx
                target_eps = get_data('EPS', target_idx)
                target_bps = get_data('BPS', target_idx)
                target_debt = get_data('부채총계', target_idx)
                target_equity = get_data('자본총계', target_idx)
                
                quarter_debt = get_data('부채총계', quarter_idx)
                quarter_equity = get_data('자본총계', quarter_idx)
                break

        fair_prev = calculate_fair_value_v2(prev_eps, prev_bps, prev_debt, prev_equity, shares)
        
        use_debt = target_debt if target_debt > 0 else quarter_debt
        use_equity = target_equity if target_equity > 0 else quarter_equity
        fair_target = calculate_fair_value_v2(target_eps, target_bps, use_debt, use_equity, shares)
        
        gap = 0
        if current_price > 0:
            gap = (fair_target - current_price) / current_price * 100
            
        diff_val = current_price - fair_prev

        return {
            'code': code, 'name': name, 'rank': rank,
            'price': current_price,
            'fair_prev': fair_prev, 'fair_target': fair_target,
            'gap': gap, 'diff_val': diff_val
        }
    except: return None

# --- 실행 ---
def run_analysis(target_list, status_text, progress_bar, worker_count):
    results = []
    total = len(target_list)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fetch_stock_data, item): item for item in target_list}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            data = future.result()
            completed += 1
            progress_bar.progress(min(completed / total, 1.0))
            
            if data and data['price'] > 0:
                status_text.text(f"⚡ {data['name']} 분석 중... ({completed}/{total})")
                results.append({
                    '종목코드': data['code'],
                    '종목명': data['name'],
                    '시총순위': data['rank'],
                    '과년도 적정주가': round(data['fair_prev'], 0),
                    '현재가': round(data['price'], 0),
                    '적정주가': round(data['fair_target'], 0),
                    '괴리율(%)': round(data['gap'], 2),
                    'Gap_Prev': data['diff_val']
                })
    return results

# ==========================================
# UI 레이아웃 시작
# ==========================================

st.markdown("<div class='main-header'>⚖️ KOSPI 분석기 2.0</div>", unsafe_allow_html=True)
st.caption("🎉 Made By 찬용")

# 공지사항
with st.expander("📘 **사용 가이드 & 산출 공식**", expanded=False):
    st.markdown("""
    <div class='info-text'>
    <span class='pastel-blue'>산출공식</span><br>
    • <b>기본:</b> (EPS × 10) + BPS<br>
    • <b>부채 과다(100%초과):</b> 위 공식 - (초과부채 ÷ 주식수)<br>
    <br>
    <span class='pastel-blue'>데이터 기준</span><br>
    • <b>과년도 적정주가:</b> 작년 확정 실적 기준<br>
    • <b>적정주가(Target):</b> 올해 예상 실적 기준 (부채 정보 없을 시 최신 분기 대입)
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- 1. 분석 설정 ---
st.subheader("1. 분석 대상 선택")

col_mode, col_speed = st.columns([1, 1])
with col_mode:
    mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색 (장바구니)"], horizontal=True)
with col_speed:
    speed = st.selectbox("분석 속도", ["빠름 (15개씩)", "보통 (8개씩)", "안정 (2개씩)"], index=1)
    worker_count = 15 if "빠름" in speed else (8 if "보통" in speed else 2)

target_list = [] 

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 200 
    
    col1, col2 = st.columns([3, 1])
    with col1:
        val = st.slider("분석할 종목 수", 10, 400, st.session_state.stock_count)
    with c2:
        num = st.number_input("직접 입력", 10, 400, st.session_state.stock_count, label_visibility="collapsed")
        if st.button("적용"):
            st.session_state.stock_count = num
            st.rerun()

    # 상위 종목 리스트 생성
    if st.button("▶️ 상위 종목 분석 시작", type="primary", use_container_width=True):
        with st.spinner("데이터 로딩 중..."):
            df_krx = get_stock_listing()
            top_n = df_krx.head(st.session_state.stock_count)
            
            for i, (idx, row) in enumerate(top_n.iterrows()):
                name = row['Name']
                if name in ["맥쿼리인프라", "SK리츠", "제이알글로벌리츠", "롯데리츠", "ESR켄달스퀘어리츠", "신한알파리츠", "맵스리얼티1", "이리츠코크렙", "코람코에너지리츠"]:
                    continue
                rank_val = row['ActualRank'] if 'ActualRank' in row else i+1
                shares = row['Shares'] if 'Shares' in row else 0
                target_list.append((str(row['Code']), name, rank_val, shares))
        
        status = st.empty()
        bar = st.progress(0)
        results = run_analysis(target_list, status, bar, worker_count)
        
        if results:
            st.session_state['analysis_result'] = pd.DataFrame(results)
            st.rerun()

elif mode == "🔍 종목 검색 (장바구니)":
    if 'basket' not in st.session_state: st.session_state.basket = []

    # 1. 검색창
    query = st.text_input("종목명 검색", placeholder="예: 삼성, 현대, 카카오")
    
    # 2. 검색 결과 (클릭형 UI)
    if query:
        df_krx = get_stock_listing()
        search_res = df_krx[df_krx['Name'].str.contains(query, case=False)].head(15) # 상위 15개만
        
        if search_res.empty:
            st.warning("검색 결과가 없습니다.")
        else:
            st.caption("👇 분석할 종목을 클릭하여 담으세요.")
            
            # Pills (알약) 형태의 선택 버튼 (Streamlit 최신 기능)
            # 0.68 버전 이상에서 st.pills 사용 가능, 여기선 multiselect 대신 columns 버튼 활용
            
            # 가로 4열 그리드
            cols = st.columns(4)
            for idx, (i, row) in enumerate(search_res.iterrows()):
                col = cols[idx % 4]
                with col:
                    # 이미 담겼는지 확인
                    is_in = any(item['code'] == str(row['Code']) for item in st.session_state.basket)
                    btn_label = f"✅ {row['Name']}" if is_in else f"➕ {row['Name']}"
                    
                    if st.button(btn_label, key=f"btn_{row['Code']}", disabled=is_in, use_container_width=True):
                        st.session_state.basket.append({
                            'code': str(row['Code']),
                            'name': row['Name'],
                            'rank': row['ActualRank'],
                            'shares': row['Shares']
                        })
                        st.rerun()

    # 3. 장바구니 현황
    st.markdown("---")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader(f"🛒 담은 종목 ({len(st.session_state.basket)}개)")
    with c2:
        if st.button("🗑️ 전체 비우기"):
            st.session_state.basket = []
            st.rerun()
            
    if st.session_state.basket:
        # 태그 형태로 보여주기
        basket_names = [item['name'] for item in st.session_state.basket]
        st.markdown(f"**목록:** {', '.join(basket_names)}")
        
        if st.button("▶️ 담은 종목 분석 시작", type="primary", use_container_width=True):
            target_list = [(i['code'], i['name'], i['rank'], i['shares']) for i in st.session_state.basket]
            
            status = st.empty()
            bar = st.progress(0)
            results = run_analysis(target_list, status, bar, worker_count)
            
            if results:
                st.session_state['analysis_result'] = pd.DataFrame(results)
                st.rerun()
    else:
        st.info("검색 후 종목을 클릭하여 담아주세요.")

# --- 3. 결과 ---
st.divider()
st.subheader("🏆 분석 결과 리포트")

if 'analysis_result' in st.session_state and not st.session_state['analysis_result'].empty:
    df = st.session_state['analysis_result']
    
    sort = st.radio("정렬 기준", ["괴리율 높은 순", "📉 저평가 심화 순 (현재가-과년도적정가)"], horizontal=True)
    
    if "괴리율" in sort:
        df = df.sort_values(by='괴리율(%)', ascending=False)
    else:
        df = df.sort_values(by='Gap_Prev', ascending=True)
    
    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "순위"
    
    cols = ['시총순위', '과년도 적정주가', '현재가', '적정주가', '괴리율(%)']
    
    # 1위 강조
    top = df.iloc[0]
    st.success(f"🥇 **{top['종목명']}** (괴리율 {top['괴리율(%)']}%)")

    # 스타일링
    def style_df(row):
        styles = []
        for col in row.index:
            if col == '괴리율(%)':
                if row[col] > 20: styles.append('color: #D47C94; font-weight: bold')
                elif row[col] < 0: styles.append('color: #5C7CFA; font-weight: bold')
                else: styles.append('')
            else: styles.append('')
        return styles

    st.dataframe(
        df.set_index('종목명')[cols].style.apply(style_df, axis=1).format("{:,.0f}", subset=['과년도 적정주가', '현재가', '적정주가']),
        height=600,
        use_container_width=True
    )
