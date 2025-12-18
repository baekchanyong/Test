import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import time
import requests
import re
from datetime import datetime, timedelta
import concurrent.futures

# --- [비밀번호 설정 구간] ---
my_password = "1414"

st.set_page_config(page_title="KOSPI 분석기", page_icon="🎨", layout="wide")

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pw = st.text_input("비밀번호를 입력하세요", type="password")
    if pw:
        if pw == my_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.write("🎉 Made By 찬용")

# --- [CSS] ---
st.markdown("""
<style>
    .responsive-header { font-size: 2.2rem; font-weight: 700; margin-bottom: 1rem; }
    .info-text { font-size: 1rem; line-height: 1.6; }
    .pastel-blue { color: #5C7CFA; font-weight: bold; }
    .pastel-red { color: #D47C94; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- 헬퍼 ---
def to_float(val):
    try:
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        clean_val = re.sub(r'[(),%]', '', str(val))
        return float(clean_val)
    except: return 0.0

# --- 리스트 로딩 ---
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
        # 주식수 = 시가총액 / 현재가
        df['Shares'] = np.where(df['Close'] > 0, df['Marcap'] / df['Close'], 0)
    else:
        df['ActualRank'] = 0
        df['Shares'] = 0
    return df

# --- [핵심] 적정주가 산출 (부채 페널티) ---
def calculate_fair_value_v2(eps, bps, debt_total, equity_total, shares):
    """
    부채비율 100% 초과 시: (EPS*10 + BPS) - (초과부채 / 주식수)
    단위: debt, equity는 억원 -> * 1억 필요
    """
    if shares <= 0: return 0
    
    base_price = (eps * 10) + bps
    
    if equity_total > 0: # 자본이 있을 때만 부채비율 계산
        debt_ratio = (debt_total / equity_total) * 100
        
        if debt_ratio > 100:
            # 초과 부채 금액 (원 단위 변환)
            excess_debt_amount = (debt_total - equity_total) * 100000000
            # 주당 페널티
            penalty_per_share = excess_debt_amount / shares
            
            return base_price - penalty_per_share
            
    return base_price

# --- 크롤링 ---
def fetch_stock_data(item):
    code, name, rank, shares = item
    current_price = 0.0
    
    # 0.0으로 초기화
    prev_eps, prev_bps, prev_debt, prev_equity = 0.0, 0.0, 0.0, 0.0
    target_eps, target_bps, target_debt, target_equity = 0.0, 0.0, 0.0, 0.0
    
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        
        # 현재가
        try:
             match = re.search(r'blind">\s*([0-9,]+)\s*<', res.text)
             if match: current_price = to_float(match.group(1))
        except: pass

        dfs = pd.read_html(res.text, encoding='cp949')
        
        for df in dfs:
            if '매출액' in df.iloc[:, 0].to_string() or '영업이익' in df.iloc[:, 0].to_string():
                df = df.set_index(df.columns[0])
                
                # 컬럼명 처리
                if isinstance(df.columns, pd.MultiIndex): cols = [str(c[1]) for c in df.columns]
                else: cols = [str(c) for c in df.columns]
                
                # --- 인덱스 찾기 ---
                est_idx = -1
                for i, c in enumerate(cols):
                    if '(E)' in c and re.search(r'\d{4}\.\d{2}', c):
                        est_idx = i
                        break
                
                prev_idx = est_idx - 1 if est_idx != -1 else -1
                if prev_idx == -1:
                    # 예상치 없으면 연간 데이터 중 가장 최근(보통 3번째)
                    for i in range(len(cols)-1, -1, -1):
                        if re.match(r'\d{4}\.\d{2}', cols[i]) and '(E)' not in cols[i]:
                            if i < 4: 
                                prev_idx = i
                                break
                    if prev_idx == -1: prev_idx = 3

                # --- 데이터 추출 함수 ---
                def get_val(row_keyword, col_index):
                    if col_index < 0 or col_index >= len(cols): return 0.0
                    try:
                        # 해당 키워드가 포함된 행 찾기
                        found = df.index[df.index.str.contains(row_keyword, na=False)]
                        if len(found) > 0:
                            v = to_float(df.loc[found[0]].iloc[col_index])
                            return v
                    except: pass
                    return 0.0

                # [중요] 최신 재무상태표 데이터 찾기 (부채/자본용)
                # 예상치 칸이 비어있으면, 오른쪽 끝(최신 분기)에서부터 거슬러 올라오며 0이 아닌 값을 찾음
                def get_latest_balance_sheet(row_keyword):
                    # 분기 데이터 쪽(뒤쪽)부터 탐색
                    for i in range(len(cols)-1, -1, -1):
                        val = get_val(row_keyword, i)
                        if val > 0: return val
                    return 0.0

                # 1. 과년도 (확정 실적)
                prev_eps = get_val('EPS', prev_idx)
                prev_bps = get_val('BPS', prev_idx)
                prev_debt = get_val('부채총계', prev_idx)
                prev_equity = get_val('자본총계', prev_idx)
                
                # 2. 목표 (예상 실적)
                target_idx = est_idx if est_idx != -1 else prev_idx
                
                target_eps = get_val('EPS', target_idx)
                target_bps = get_val('BPS', target_idx)
                
                # [수정] 부채와 자본은 예상치 칸이 0이면 최신 확정치를 쓴다. (매우 중요)
                temp_debt = get_val('부채총계', target_idx)
                target_debt = temp_debt if temp_debt > 0 else get_latest_balance_sheet('부채총계')
                
                temp_equity = get_val('자본총계', target_idx)
                target_equity = temp_equity if temp_equity > 0 else get_latest_balance_sheet('자본총계')
                
                break

        # 산출
        fair_prev = calculate_fair_value_v2(prev_eps, prev_bps, prev_debt, prev_equity, shares)
        fair_target = calculate_fair_value_v2(target_eps, target_bps, target_debt, target_equity, shares)
        
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
                status_text.text(f"⚡ {data['name']} 분석 완료")
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
# UI
# ==========================================

st.markdown("<div class='main-header'>⚖️ KOSPI 분석기 1.0Ver</div>", unsafe_allow_html=True)

with st.expander("📘 **공지사항 & 산출공식**", expanded=True):
    st.markdown("""
    <div class='info-text'>
    <span class='pastel-blue'>산출공식</span><br>
    • <b>기본:</b> (EPS × 10) + BPS<br>
    • <b>부채 과다(100%초과):</b> (EPS × 10) + BPS - <b>[(초과부채) ÷ 주식수]</b><br>
    <br>
    <span class='pastel-blue'>데이터 기준</span><br>
    • <b>부채/자본:</b> 예상치 데이터가 없으면 <b>최신 분기 확정치</b>를 찾아 적용합니다.<br>
    </div>
    """, unsafe_allow_html=True)

with st.expander("🛠️ **패치노트**", expanded=False):
    st.markdown("""
    <div class='info-text'>
    <b>(25.11.26) 1.0Ver : 최초배포</b><br>
    &nbsp; • 분석 제외종목 : 맥쿼리인프라, SK리츠 등 부동산/인프라 펀드 제외<br>
    </div>
    """, unsafe_allow_html=True)

st.divider()

st.header("1. 분석 설정")
col_mode, col_speed = st.columns([1, 1])
with col_mode:
    mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색 (장바구니)"], horizontal=True)
with col_speed:
    speed = st.selectbox("분석 속도", ["빠름 (15개씩)", "보통 (8개씩)", "안정 (2개씩)"], index=1)
    worker_count = 15 if "빠름" in speed else (8 if "보통" in speed else 2)

target_list = [] 

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 200 
    c1, c2 = st.columns([3, 1])
    with c1:
        val = st.slider("종목 수", 10, 400, st.session_state.stock_count)
    with c2:
        num = st.number_input("직접 입력", 10, 400, st.session_state.stock_count, label_visibility="collapsed")
        if st.button("적용"):
            st.session_state.stock_count = num
            st.rerun()

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
    query = st.text_input("종목명 검색", placeholder="예: 삼성")
    
    if query:
        df_krx = get_stock_listing()
        res = df_krx[df_krx['Name'].str.contains(query, case=False)].head(15)
        if res.empty: st.warning("결과 없음")
        else:
            st.caption("👇 클릭하여 담기")
            cols = st.columns(3) # 3열 그리드
            for idx, (i, row) in enumerate(res.iterrows()):
                col = cols[idx % 3]
                with col:
                    with st.container():
                        c_btn, c_txt = st.columns([0.3, 0.7])
                        is_in = any(x['code'] == str(row['Code']) for x in st.session_state.basket)
                        with c_btn:
                            if is_in: st.button("✅", key=f"d_{row['Code']}", disabled=True)
                            else:
                                if st.button("➕", key=f"a_{row['Code']}"):
                                    st.session_state.basket.append({
                                        'code': str(row['Code']), 'name': row['Name'],
                                        'rank': row['ActualRank'], 'shares': row['Shares']
                                    })
                                    st.rerun()
                        with c_txt:
                            st.markdown(f"**{row['Name']}**")
                            st.caption(f"{row['Code']}")
                    st.markdown("---")

    st.subheader(f"🛒 담은 종목 ({len(st.session_state.basket)}개)")
    if len(st.session_state.basket) > 0:
        if st.button("🗑️ 비우기"):
            st.session_state.basket = []
            st.rerun()
        
        # 목록 보여주기
        b_df = pd.DataFrame(st.session_state.basket)
        st.dataframe(b_df[['name', 'code']], hide_index=True, use_container_width=True)

        if st.button("▶️ 담은 종목 분석 시작", type="primary", use_container_width=True):
            target_list = [(x['code'], x['name'], x['rank'], x['shares']) for x in st.session_state.basket]
            status = st.empty()
            bar = st.progress(0)
            results = run_analysis(target_list, status, bar, worker_count)
            if results:
                st.session_state['analysis_result'] = pd.DataFrame(results)
                st.rerun()

st.divider()
st.subheader("🏆 분석 결과")

if 'analysis_result' in st.session_state and not st.session_state['analysis_result'].empty:
    df = st.session_state['analysis_result']
    sort = st.radio("정렬", ["괴리율 높은 순", "📉 저평가 심화 순"], horizontal=True)
    
    if "괴리율" in sort: df = df.sort_values(by='괴리율(%)', ascending=False)
    else: df = df.sort_values(by='Gap_Prev', ascending=True)
    
    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "순위"
    
    cols = ['시총순위', '과년도 적정주가', '현재가', '적정주가', '괴리율(%)']
    top = df.iloc[0]
    st.success(f"🥇 **{top['종목명']}** (괴리율 {top['괴리율(%)']}%)")

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
