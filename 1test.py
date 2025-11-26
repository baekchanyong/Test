import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import time
import requests
import re
from datetime import datetime, timedelta
import concurrent.futures # 병렬 처리를 위한 라이브러리

# --- [비밀번호 설정 구간 시작] ---
# 원하는 숫자로 바꾸기
my_password = "1478"

# 설정: 페이지 기본 구성
st.set_page_config(page_title="V55 가치투자 분석기", page_icon="🎨", layout="wide")

# 화면에 비밀번호 입력창을 만듭니다.
password_input = st.text_input("비밀번호를 입력하세요", type="password")

if password_input != my_password:
    st.error("비밀번호가 틀렸거나 입력되지 않았습니다. 주인에게 물어보세요")
    st.stop()

st.write("🎉 Good Luck!")
# --- [비밀번호 설정 구간 끝] ---


# --- [CSS] 스타일 적용 ---
st.markdown("""
<style>
    .responsive-header {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }
    @media (max-width: 600px) {
        .responsive-header { font-size: 1.5rem; }
    }
    .info-text { font-size: 1rem; line-height: 1.6; }
    .pastel-blue { color: #ABC4FF; font-weight: bold; }
    .pastel-red { color: #D47C94; font-weight: bold; }
    @media (max-width: 600px) { .info-text { font-size: 0.9rem; } }
</style>
""", unsafe_allow_html=True)

# --- 헬퍼 함수 ---
def to_float(val):
    try:
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        # 괄호, 콤마, 퍼센트 제거
        clean_val = re.sub(r'[(),%]', '', str(val))
        return float(clean_val)
    except: return 0.0

# --- [금리] 한국은행 기준금리 ---
def get_bok_base_rate():
    url = "https://finance.naver.com/marketindex/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=2)
        match = re.search(r'한국은행 기준금리.*?([0-9]{1}\.[0-9]{2})', response.text, re.DOTALL)
        return float(match.group(1)) if match else 3.25
    except: return 3.25

# --- 공포탐욕지수 (주봉) ---
def calculate_fear_greed_weekly(code):
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
        df = fdr.DataReader(code, start_date, end_date)
        
        if df.empty: return 50
        
        df_weekly = df.resample('W-FRI').agg({'Close': 'last'}).dropna()
        if len(df_weekly) < 20: return 50
        
        # RSI
        delta = df_weekly['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # 이격도
        ma20 = df_weekly['Close'].rolling(window=20).mean()
        disparity = (df_weekly['Close'] / ma20) * 100
        disparity_score = disparity.apply(lambda x: 0 if x < 90 else (100 if x > 110 else (x - 90) * 5))
        
        val = (rsi.iloc[-1] * 0.5) + (disparity_score.iloc[-1] * 0.5)
        return 50 if pd.isna(val) else val
    except: return 50

# --- [핵심] 개별 종목 데이터 크롤링 (병렬 처리용) ---
def fetch_stock_data(item):
    code, name, rank = item
    try:
        # 1. 네이버 금융에서 EPS, BPS, 현재가 크롤링
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=3)
        dfs = pd.read_html(res.text, encoding='cp949')
        
        eps, bps, current_price = 0.0, 0.0, 0.0
        
        # 현재가 찾기 (상단 테이블)
        for df in dfs:
            if df.shape[1] >= 2 and ('현재가' in str(df.iloc[:, 0].values) or '현재가' in str(df.columns)):
                 # 간단히 종가 정보가 있는 테이블이라고 가정
                 pass
        
        # 좀 더 확실한 현재가 (FDR로 가져오는게 가장 확실하지만 속도를 위해 여기서 파싱 시도)
        # 만약 크롤링 실패시 FDR로 대체
        try:
             # 네이버 페이지 내 '현재가' text search
             match = re.search(r'blind">\s*([0-9,]+)\s*<', res.text)
             if match: current_price = to_float(match.group(1))
        except: pass

        # 펀더멘털 (EPS, BPS) 찾기
        for df in dfs:
            str_df = df.to_string()
            if 'EPS' in str_df or 'BPS' in str_df:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                
                # 최근 결산일 기준 값 찾기 (데이터프레임 순회)
                for idx, row in df.iterrows():
                    row_name = str(row.iloc[0])
                    vals = row.iloc[1:].tolist()
                    
                    # 가장 최근의 유효한(0이 아닌) 값을 뒤에서부터 찾음
                    valid_val = 0.0
                    for v in reversed(vals):
                        v_float = to_float(v)
                        if v_float > 0: 
                            valid_val = v_float
                            break
                    
                    if 'EPS' in row_name or '주당순이익' in row_name:
                        if valid_val > 0: eps = valid_val
                    if 'BPS' in row_name or '주당순자산' in row_name:
                        if valid_val > 0: bps = valid_val
                
                if eps > 0 and bps > 0: break
        
        # 크롤링 실패 시 보완 (FDR로 가격 재확인)
        if current_price == 0:
            df_price = fdr.DataReader(code, datetime.now().strftime('%Y-%m-%d'))
            if not df_price.empty: current_price = to_float(df_price['Close'].iloc[-1])

        # 2. 공포탐욕지수 계산
        fg_score = calculate_fear_greed_weekly(code)
        
        return {
            'code': code, 'name': name, 'rank': rank,
            'price': current_price, 'eps': eps, 'bps': bps,
            'fg_score': fg_score
        }
    except Exception as e:
        return None

# --- 분석 실행 (Thread Pool 사용) ---
def run_analysis_parallel(target_list, applied_rate, status_text, progress_bar):
    results = []
    total = len(target_list)
    
    # max_workers=15 : 15개씩 동시에 처리 (속도 대폭 향상)
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        # 작업 목록 생성
        futures = {executor.submit(fetch_stock_data, item): item for item in target_list}
        
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            data = future.result()
            completed_count += 1
            
            # 진행률 업데이트
            progress_bar.progress(min(completed_count / total, 1.0))
            if data:
                status_text.text(f"⚡ [{completed_count}/{total}] {data['name']} 분석 완료")
                
                # 계산 로직
                eps, bps = data['eps'], data['bps']
                
                # 데이터가 0이면 스킵하지 않고 일단 0으로 표시하되 경고 (또는 리스트 포함)
                # 여기서는 데이터 있는 것만 계산
                if eps == 0 and bps == 0: continue

                roe = (eps / bps * 100) if bps > 0 else 0
                
                earnings_value = 0
                if applied_rate > 0: earnings_value = eps / (applied_rate / 100)
                
                base_fair = (earnings_value * 0.7) + (bps * 0.3)
                sentiment = 1 + ((50 - data['fg_score']) / 50 * 0.1)
                fair_price = base_fair * sentiment
                
                gap = 0
                if data['price'] > 0:
                    gap = (fair_price - data['price']) / data['price'] * 100
                
                results.append({
                    '종목코드': data['code'],
                    '종목명': data['name'],
                    '시총순위': data['rank'],
                    '현재가': round(data['price'], 0),
                    '적정주가': round(fair_price, 0),
                    '괴리율': round(gap, 2),
                    '공포지수': round(data['fg_score'], 1),
                    'ROE(%)': round(roe, 2),
                    'EPS': round(eps, 0),
                    'BPS': round(bps, 0)
                })

    progress_bar.empty()
    if results:
        st.session_state['analysis_result'] = pd.DataFrame(results)
        return True
    return False

# --- 메인 UI ---
st.markdown("<div class='responsive-header'>⚖️ V55 가치투자 분석기 (Parallel)</div>", unsafe_allow_html=True)

# 1. 설명서
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
    &nbsp; • <b>원리:</b> 공포 구간일수록 적정주가를 높게, 탐욕 구간일수록 낮게 보정
    </div>
    """, unsafe_allow_html=True)

# 2. 패치노트
with st.expander("🛠️ **패치노트**", expanded=False):
    st.markdown("내용")

st.divider()

# --- 1. 설정 ---
st.header("1. 분석 설정")

mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색"], horizontal=True)
target_list = [] # (Code, Name, Rank) 튜플 리스트

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 50 # 기본값 줄임 (속도 체감 위해)

    def update_from_slider(): st.session_state.stock_count = st.session_state.slider_key
    def apply_manual_input(): st.session_state.stock_count = st.session_state.num_key

    c1, c2 = st.columns([3, 1])
    with c1:
        st.slider("종목 수 조절", 10, 200, key='slider_key', value=st.session_state.stock_count, on_change=update_from_slider)
    with c2:
        st.number_input("직접 입력", 10, 400, key='num_key', value=st.session_state.stock_count)
        if st.button("✅ 수치 적용", on_click=apply_manual_input): st.rerun()

elif mode == "🔍 종목 검색":
    query = st.text_input("종목명 검색", placeholder="예: 삼성")
    if query:
        try:
            with st.spinner("목록 검색 중..."):
                df_krx = fdr.StockListing('KRX')
                res = df_krx[df_krx['Name'].str.contains(query, case=False)]
                if res.empty: st.error("결과 없음")
                else:
                    picks = st.multiselect("선택", res['Name'].tolist(), default=res['Name'].tolist()[:5])
                    # 선택된 종목을 타겟 리스트 형태로 변환
                    selected = res[res['Name'].isin(picks)]
                    for idx, row in selected.iterrows():
                        target_list.append((str(row['Code']), row['Name'], 1))
        except: st.error("오류 발생")

# --- 2. 실행 ---
st.divider()
if st.button("▶️ 분석 시작 (Start)", type="primary", use_container_width=True):
    
    # 시가총액 모드일 때 리스트 생성
    if mode == "🏆 시가총액 상위":
        with st.spinner("기초 데이터 준비 중..."):
            df_krx = fdr.StockListing('KRX')
            if 'Marcap' in df_krx.columns:
                df_krx = df_krx.sort_values(by='Marcap', ascending=False)
            
            top_n = df_krx.head(st.session_state.stock_count)
            target_list = []
            for i, (idx, row) in enumerate(top_n.iterrows()):
                target_list.append((str(row['Code']), row['Name'], i+1))
    
    if not target_list:
        st.warning("분석할 종목이 없습니다.")
        st.stop()

    status_box = st.empty()
    status_box.info("🇰🇷 금리 조회 & 멀티 프로세싱 준비...")
    
    bok_rate = get_bok_base_rate()
    applied_rate = bok_rate if bok_rate else 3.25
    
    status_box.success(f"✅ 기준금리 {applied_rate}% | 병렬 분석 시작...")
    time.sleep(0.5)
    
    p_bar = st.progress(0)
    is_success = run_analysis_parallel(target_list, applied_rate, status_box, p_bar)
    
    if is_success:
        status_box.success(f"✅ 분석 완료!")
        time.sleep(0.5)
        st.rerun()

# --- 3. 결과 ---
st.divider()
st.header("🏆 분석 결과")

sort_opt = st.radio("정렬 기준", ["괴리율 높은 순", "ROE 높은 순", "공포지수 낮은 순"], horizontal=True)

if st.button("🔄 결과 새로고침"): st.rerun()

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

    def style_dataframe(row):
        styles = []
        for col in row.index:
            color = '#BAA4D3'
            weight = 'normal'
            if col == '괴리율':
                val = row['괴리율']
                if val > 20: color = '#D47C94'; weight = 'bold'
                elif val < 0: color = '#ABC4FF'; weight = 'bold'
            elif col == '공포지수':
                val = row['공포지수']
                if val <= 30: color = '#D47C94'; weight = 'bold'
                elif val >= 70: color = '#ABC4FF'; weight = 'bold'
            styles.append(f'color: {color}; font-weight: {weight}')
        return styles

    st.dataframe(
        df_display[cols].style.apply(style_dataframe, axis=1).format("{:,.0f}", subset=['현재가', '적정주가', 'EPS', 'BPS']),
        height=800,
        use_container_width=True
    )
else:
    st.info("👈 위에서 [분석 시작] 버튼을 눌러주세요.")
