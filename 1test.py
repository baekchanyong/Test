import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import time
import requests
import re
from datetime import datetime, timedelta

# --- [비밀번호 설정 구간 시작] ---
# 원하는 숫자로 바꾸기
my_password = "1478"

# 설정: 페이지 기본 구성 (비밀번호 입력 전)
st.set_page_config(page_title="V55 가치투자 분석기", page_icon="🎨", layout="wide")

# 화면에 비밀번호 입력창을 만듭니다.
password_input = st.text_input("비밀번호를 입력하세요", type="password")

# 비밀번호가 맞는지 확인합니다.
if password_input != my_password:
    st.error("비밀번호가 틀렸거나 입력되지 않았습니다. 주인에게 물어보세요")
    st.stop()  # 비밀번호가 틀리면 여기서 멈추고, 아래 코드를 보여주지 않습니다.

st.write("🎉 Good Luck!")
# --- [비밀번호 설정 구간 끝] ---


# --- [CSS] 모바일 최적화 및 파스텔 톤 스타일 적용 ---
st.markdown("""
<style>
    /* 타이틀 반응형 크기 조정 */
    .responsive-header {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }
    @media (max-width: 600px) {
        .responsive-header {
            font-size: 1.5rem; /* 모바일에서는 작게 */
        }
    }
    /* 설명 텍스트 크기 및 줄간격 조정 */
    .info-text {
        font-size: 1rem;
        line-height: 1.6;
    }
    /* 파스텔 톤 텍스트 스타일 */
    .pastel-blue {
        color: #ABC4FF;
        font-weight: bold;
    }
    .pastel-red {
        color: #D47C94;
        font-weight: bold;
    }
    @media (max-width: 600px) {
        .info-text {
            font-size: 0.9rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# --- 헬퍼 함수 ---
def to_float(val):
    try:
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        clean_val = str(val).replace(',', '').replace('%', '')
        return float(clean_val)
    except: return 0.0

# --- [금리] 한국은행 기준금리 ---
def get_bok_base_rate():
    url = "https://finance.naver.com/marketindex/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=2)
        response.encoding = 'cp949'
        html = response.text
        match = re.search(r'한국은행 기준금리.*?([0-9]{1}\.[0-9]{2})', html, re.DOTALL)
        if match: return float(match.group(1))
        return 3.25 
    except: return 3.25

# --- 공포탐욕지수 (주봉 기준) ---
def calculate_fear_greed_weekly(df_daily):
    if df_daily.empty: return 50
    
    try:
        # 주봉 변환 (금요일 기준)
        df_weekly = df_daily.resample('W-FRI').agg({'Close': 'last'}).dropna()
    except: return 50

    if len(df_weekly) < 20: return 50
    
    # RSI (14주)
    delta = df_weekly['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 이격도 (20주)
    ma20 = df_weekly['Close'].rolling(window=20).mean()
    disparity = (df_weekly['Close'] / ma20) * 100
    
    # 이격도 점수화 (90~110 사이 0, 이탈 시 점수 부여)
    disparity_score = disparity.apply(lambda x: 0 if x < 90 else (100 if x > 110 else (x - 90) * 5))
    
    try:
        val = (rsi.iloc[-1] * 0.5) + (disparity_score.iloc[-1] * 0.5)
        return 50 if pd.isna(val) else val
    except: return 50

# --- 분석 실행 (속도 최적화 버전) ---
def run_analysis_core(target_stocks, applied_rate, status_text, progress_bar):
    today_str = datetime.now().strftime('%Y-%m-%d')
    # 차트 데이터용 시작일 (약 2년 전)
    chart_start = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
    
    total = len(target_stocks)
    results = [] 
    target_stocks = target_stocks.reset_index(drop=True)

    for step, (idx, row) in enumerate(target_stocks.iterrows()):
        code = str(row['Code']) # FDR의 Symbol 혹은 Code
        name = row['Name']
        marcap_rank = step + 1 # 순위 (이미 정렬된 데이터 가정)

        if name in ["맥쿼리인프라", "SK리츠"]: continue
        
        # 진행률 업데이트
        progress_bar.progress(min((step + 1) / total, 1.0))
        status_text.text(f"🚀 [{step+1}/{total}] {name} 고속 분석 중...")
        
        try:
            current_price = to_float(row.get('Close', 0))
            
            # [속도 개선] 네이버 크롤링 제거 -> FDR 데이터프레임의 값 즉시 사용
            # FDR StockListing('KRX')에는 이미 EPS, BPS 정보가 포함되어 있습니다.
            eps = to_float(row.get('EPS', 0))
            bps = to_float(row.get('BPS', 0))
            
            roe = 0
            if bps > 0: roe = (eps / bps) * 100
            
            # 차트 데이터 가져오기 (이 부분은 시간이 걸리므로 유지하되 최소화)
            fg_score = 50
            try:
                df_chart = fdr.DataReader(code, chart_start, today_str)
                if not df_chart.empty:
                    fg_score = calculate_fear_greed_weekly(df_chart)
            except: pass

            # 수익7:자산3 모델
            earnings_value = 0
            if applied_rate > 0:
                earnings_value = eps / (applied_rate / 100)
            
            asset_value = bps
            base_fair_price = (earnings_value * 0.7) + (asset_value * 0.3)
            
            sentiment_factor = 1 + ((50 - fg_score) / 50 * 0.1)
            fair_price = base_fair_price * sentiment_factor
            
            gap = 0
            if current_price > 0:
                gap = (fair_price - current_price) / current_price * 100
            
            results.append({
                '종목코드': code,
                '종목명': name,
                '시총순위': marcap_rank,
                '현재가': round(current_price, 0),
                '적정주가': round(fair_price, 0),
                '괴리율': round(gap, 2),
                '공포지수': round(fg_score, 1),
                'ROE(%)': round(roe, 2),
                'EPS': round(eps, 0),
                'BPS': round(bps, 0)
            })
            
        except: continue

    progress_bar.empty()
    
    if results:
        st.session_state['analysis_result'] = pd.DataFrame(results)
        return True
    return False

# --- 메인 UI ---

# 타이틀
st.markdown("<div class='responsive-header'>⚖️ V55 가치투자 분석기</div>", unsafe_allow_html=True)

# 1. 설명서 (수정 요청 반영)
with st.expander("📘 **[필독] 산출 공식 및 원리**", expanded=True):
    st.markdown("""
    <div class='info-text'>
    <b>1. 적정주가 (수익중심 모델)</b><br>
    &nbsp; • <b>수익가치(70%):</b> (EPS ÷ 한국은행 기준금리)<br>
    &nbsp; • <b>자산가치(30%):</b> BPS<br>
    &nbsp; • <b>최종:</b> (수익가치×0.7 + 자산가치×0.3) × 심리보정<br><br>
    
    <span class='pastel-blue'>파스텔 블루 입력</span><br>
    <span class='pastel-red'>파스텔 레드 입력</span><br><br>
    
    <b>2. 공포탐욕지수 (주봉 기준)</b><br>
    &nbsp; • <b>구성:</b> RSI(14주) 50% + 이격도(20주) 50%<br>
    &nbsp; • <b>해석:</b> 30점 이하(공포/매수), 70점 이상(탐욕/매도)<br><br>

    <b>3. 심리보정 수식</b><br>
    &nbsp; • <b>공식:</b> 1 + ((50 - 공포지수) ÷ 50 × 0.1)<br>
    &nbsp; • <b>원리:</b> 공포 구간일수록 적정주가를 높게(매수 유도), 탐욕 구간일수록 낮게 보정
    </div>
    """, unsafe_allow_html=True)

# 2. 패치노트 (신규 추가)
with st.expander("🛠️ **패치노트**", expanded=False):
    st.markdown("내용")

st.divider()

# --- 1. 설정 ---
st.header("1. 분석 설정")

mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색"], horizontal=True)
target_stocks = pd.DataFrame()

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state:
        st.session_state.stock_count = 200

    def update_from_slider():
        st.session_state.stock_count = st.session_state.slider_key

    def apply_manual_input():
        st.session_state.stock_count = st.session_state.num_key

    c1, c2 = st.columns([3, 1])
    with c1:
        st.slider(
            "종목 수 조절", 10, 400, 
            key='slider_key', 
            value=st.session_state.stock_count, 
            on_change=update_from_slider
        )
    with c2:
        st.number_input(
            "직접 입력", 10, 400, 
            key='num_key', 
            value=st.session_state.stock_count
        )
        if st.button("✅ 수치 적용", on_click=apply_manual_input):
            st.rerun()

elif mode == "🔍 종목 검색":
    query = st.text_input("종목명 검색", placeholder="예: 삼성")
    if query:
        try:
            with st.spinner("검색 중..."):
                # KRX 전체 리스트 불러오기 (한번만 로딩됨)
                df_krx = fdr.StockListing('KRX')
                res = df_krx[df_krx['Name'].str.contains(query, case=False)]
                if res.empty: st.error("결과 없음")
                else:
                    picks = st.multiselect("선택", res['Name'].tolist(), default=res['Name'].tolist()[:5])
                    target_stocks = res[res['Name'].isin(picks)]
        except: st.error("오류")

# --- 2. 실행 ---
st.divider()
if st.button("▶️ 분석 시작 (Start)", type="primary", use_container_width=True):
    
    if mode == "🏆 시가총액 상위":
        with st.spinner("데이터 로딩 중..."):
            # FDR StockListing 사용 (KRX 데이터는 EPS/BPS 등을 포함함)
            df_krx = fdr.StockListing('KRX')
            
            # KOSPI만 필터링하거나 전체 사용 (여기서는 시총 상위이므로 전체 정렬)
            # Marcap(시가총액) 기준 내림차순 정렬
            if 'Marcap' in df_krx.columns:
                df_krx = df_krx.sort_values(by='Marcap', ascending=False)
            
            final_target = df_krx.head(st.session_state.stock_count)
    else:
        if target_stocks.empty:
            st.warning("종목을 선택해주세요.")
            st.stop()
        final_target = target_stocks

    status_box = st.empty()
    status_box.info("🇰🇷 한국은행 기준금리 조회 중...")
    
    bok_rate = get_bok_base_rate()
    applied_rate = bok_rate if bok_rate else 3.25
    
    status_box.success(f"✅ 기준금리 **{applied_rate}%** 적용 | 고속 분석을 시작합니다...")
    time.sleep(0.5)
    
    p_bar = st.progress(0)
    is_success = run_analysis_core(final_target, applied_rate, status_box, p_bar)
    
    if is_success:
        status_box.success(f"✅ 분석 완료!")
        time.sleep(0.5)
        st.rerun()

# --- 3. 결과 ---
st.divider()
st.header("🏆 분석 결과")

sort_opt = st.radio("정렬 기준", ["괴리율 높은 순", "ROE 높은 순", "공포지수 낮은 순"], horizontal=True)

if st.button("🔄 결과 새로고침"): st.rerun()

# 세션 스테이트에서 데이터 확인
if 'analysis_result' in st.session_state and not st.session_state['analysis_result'].empty:
    df = st.session_state['analysis_result']
    
    if "괴리율" in sort_opt: df = df.sort_values(by='괴리율', ascending=False)
    elif "ROE" in sort_opt: df = df.sort_values(by='ROE(%)', ascending=False)
    else: df = df.sort_values(by='공포지수', ascending=True)
    
    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "순위"
    
    cols = ['시총순위', '현재가', '적정주가', '괴리율', '공포지수', 'ROE(%)', 'EPS', 'BPS']
    df_display = df.set_index('종목명', append=True)
    
    top = df.iloc[0]
    st.info(f"🥇 **1위: {top['종목명']}** (시총 {top['시총순위']}위) | 괴리율: {top['괴리율']}%")

    # 스타일 적용 함수
    def style_dataframe(row):
        styles = []
        for col in row.index:
            color = '#BAA4D3' # 기본 파스텔 보라
            weight = 'normal'
            
            # 1. 괴리율 색상
            if col == '괴리율':
                val = row['괴리율']
                if val > 20:
                    color = '#D47C94' # 파스텔 레드 (저평가)
                    weight = 'bold'
                elif val < 0:
                    color = '#ABC4FF' # 파스텔 블루 (고평가)
                    weight = 'bold'
            
            # 2. 공포지수 색상
            elif col == '공포지수':
                val = row['공포지수']
                if val <= 30:
                    color = '#D47C94' # 파스텔 레드 (공포/매수)
                    weight = 'bold'
                elif val >= 70:
                    color = '#ABC4FF' # 파스텔 블루 (탐욕/매도)
                    weight = 'bold'
            
            styles.append(f'color: {color}; font-weight: {weight}')
            
        return styles

    st.dataframe(
        df_display[cols].style.apply(style_dataframe, axis=1).format("{:,.0f}", subset=['현재가', '적정주가', 'EPS', 'BPS']),
        height=800,
        use_container_width=True
    )
else:
    st.info("👈 위에서 [분석 시작] 버튼을 눌러주세요.")
