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
st.set_page_config(page_title="KOSPI 분석기", page_icon="🎨", layout="wide")

# 화면에 비밀번호 입력창을 만듭니다.
password_input = st.text_input("비밀번호를 입력하세요", type="password")

if password_input != my_password:
    st.error("비밀번호를 입력하고 엔터를 누르면 실행됩니다.")
    st.stop()

st.write("🎉 Made By 찬용")
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
    .pastel-blue { color: #5C7CFA; font-weight: bold; } /* 라이트모드 가독성을 위해 조금 더 진한 파스텔 블루 */
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

# --- [캐싱 적용] 종목 리스트 로딩 최적화 ---
# 이 함수는 한 번 실행되면 결과를 메모리에 저장해두어 속도를 높입니다.
@st.cache_data
def get_stock_listing():
    # KRX 전체 데이터를 가져온 뒤 KOSPI만 필터링해야 Marcap 등 정보가 온전함
    df = fdr.StockListing('KRX')
    
    # [수정] KOSPI 종목만 필터링 (코스닥/코넥스 제외하여 네이버 코스피 순위와 일치시킴)
    if 'Market' in df.columns:
        df = df[df['Market'] == 'KOSPI']
    
    # [수정] 미리 시가총액(Marcap) 순으로 정렬하고 전체 순위(ActualRank)를 매겨둡니다.
    if 'Marcap' in df.columns:
        df = df.sort_values(by='Marcap', ascending=False)
        df['ActualRank'] = range(1, len(df) + 1) # 1위부터 순위 부여
    else:
        df['ActualRank'] = 0
    return df

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
        # 헤더를 추가하여 봇 탐지 회피 확률 높임
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://finance.naver.com/'
        }
        res = requests.get(url, headers=headers, timeout=5) 
        dfs = pd.read_html(res.text, encoding='cp949')
        
        eps, bps, current_price = 0.0, 0.0, 0.0
        
        # 현재가 파싱
        try:
             match = re.search(r'blind">\s*([0-9,]+)\s*<', res.text)
             if match: current_price = to_float(match.group(1))
        except: pass

        # 펀더멘털 (EPS, BPS) 찾기
        for df in dfs:
            str_df = df.to_string()
            if 'EPS' in str_df or 'BPS' in str_df:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                
                for idx, row in df.iterrows():
                    row_name = str(row.iloc[0])
                    vals = row.iloc[1:].tolist()
                    
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
        
        # 크롤링 실패 시 보완
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
        # 에러가 나도 기본값 반환
        return {
            'code': code, 'name': name, 'rank': rank,
            'price': 0, 'eps': 0, 'bps': 0,
            'fg_score': 50
        }

# --- 분석 실행 (Thread Pool + Worker Count 적용) ---
def run_analysis_parallel(target_list, applied_rate, status_text, progress_bar, worker_count):
    results = []
    total = len(target_list)
    
    # [수정] 사용자가 선택한 worker_count 적용
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fetch_stock_data, item): item for item in target_list}
        
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            data = future.result()
            completed_count += 1
            
            progress_bar.progress(min(completed_count / total, 1.0))
            
            if data:
                status_text.text(f"⚡ [{completed_count}/{total}] {data['name']} 분석 완료")
                
                eps, bps = data['eps'], data['bps']
                price = data['price']

                roe = (eps / bps * 100) if bps > 0 else 0
                
                earnings_value = 0
                if applied_rate > 0: earnings_value = eps / (applied_rate / 100)
                
                base_fair = (earnings_value * 0.7) + (bps * 0.3)
                sentiment = 1 + ((50 - data['fg_score']) / 50 * 0.1)
                fair_price = base_fair * sentiment
                
                gap = 0
                if price > 0:
                    gap = (fair_price - price) / price * 100
                
                results.append({
                    '종목코드': data['code'],
                    '종목명': data['name'],
                    '시총순위': data['rank'],
                    '현재가': round(price, 0),
                    '적정주가': round(fair_price, 0),
                    '괴리율': round(gap, 2),
                    '공포지수': round(data['fg_score'], 1),
                    'ROE(%)': round(roe, 2),
                    'EPS': round(eps, 0),
                    'BPS': round(bps, 0)
                })

    progress_bar.empty()
    if results:
        # st.session_state는 메모리에만 저장되며, 브라우저를 닫으면 사라집니다 (파일 생성 X)
        st.session_state['analysis_result'] = pd.DataFrame(results)
        return True
    return False

# --- 메인 UI ---
st.markdown("<div class='responsive-header'>⚖️ KOSPI 분석기 1.1Ver</div>", unsafe_allow_html=True)

# 1. 설명서
with st.expander("📘 **공지사항 및 산출공식**", expanded=True):
    st.markdown("""
    <div class='info-text'>

    <span class='pastel-blue'>공지사항</span><br>
    <span class='pastel-red'># 적정주가는 절대적인 값보다, 상대적으로 봐야됨</span><br>
    <span class='pastel-red'># 괴리율 높고,공포지수 낮을수록 매수대상으로 판단</span><br>
    <br><br>

    <span class='pastel-blue'>산출공식</span><br>
    <b>1. 적정주가(수익중심 모델)</b><br>
    &nbsp; • <b>수익가치(70%):</b> (EPS ÷ 한국은행 기준금리)<br>
    &nbsp; • <b>자산가치(30%):</b> BPS<br>
    &nbsp; • <b>최종:</b> (수익가치×0.7 + 자산가치×0.3) × 심리보정<br><br>
    
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
    st.markdown("""
    <div class='info-text'>
    
    <b>(25.11.26) 1.0Ver : 최초배포</b><br>
    &nbsp; • 분석 필터링 추가: 맥쿼리인프라, SK리츠 등 제외<br>
    &nbsp; • 로딩 속도 최적화 적용 (캐싱)<br>
    &nbsp; • 시총순위: 검색 목록 기준이 아닌 '실제 코스피 순위'로 개선<br>
    <br>
    <b>(25.11.26) 1.1Ver : 긴급수정</b><br>
    &nbsp; • 시총순위 오류 수정: 코스닥 종목이 섞여있던 문제를 수정하여 'KOSPI' 종목만 정확히 집계하도록 변경<br>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- 1. 설정 ---
st.header("1. 분석 설정")

# [추가] 분석 속도 선택 옵션
speed_option = st.radio(
    "분석 속도 설정",
    ["🚀 빠른 분석 (데이터 15개씩 / 누락 가능성 있음)", "⚖️ 보통 분석 (데이터 8개씩 / 권장)", "🐢 느린 분석 (데이터 2개씩 / 매우 안정적)"],
    index=1 # 기본값: 보통 분석
)

# 선택된 옵션에 따라 worker_count 설정
if "빠른" in speed_option:
    worker_count = 15
elif "보통" in speed_option:
    worker_count = 8
else:
    worker_count = 2

st.divider()

mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색"], horizontal=True)
target_list = [] 

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 200 

    def update_from_slider(): st.session_state.stock_count = st.session_state.slider_key
    def apply_manual_input(): st.session_state.stock_count = st.session_state.num_key

    c1, c2 = st.columns([3, 1])
    with c1:
        st.slider("종목 수 조절", 10, 400, key='slider_key', value=st.session_state.stock_count, on_change=update_from_slider)
    with c2:
        st.number_input("직접 입력", 10, 400, key='num_key', value=st.session_state.stock_count)
        if st.button("✅ 수치 적용", on_click=apply_manual_input): st.rerun()

elif mode == "🔍 종목 검색":
    query = st.text_input("종목명 검색", placeholder="예: 삼성")
    if query:
        try:
            with st.spinner("목록 검색 중..."):
                # [수정] 캐싱된 함수 사용
                df_krx = get_stock_listing()
                res = df_krx[df_krx['Name'].str.contains(query, case=False)]
                if res.empty: st.error("결과 없음")
                else:
                    picks = st.multiselect("선택", res['Name'].tolist(), default=res['Name'].tolist()[:5])
                    selected = res[res['Name'].isin(picks)]
                    for idx, row in selected.iterrows():
                        # [수정] 실제 순위(ActualRank)를 사용하도록 변경
                        rank_val = row['ActualRank'] if 'ActualRank' in row else 0
                        target_list.append((str(row['Code']), row['Name'], rank_val))
        except: st.error("오류 발생")

# --- 2. 실행 ---
st.divider()
if st.button("▶️ 분석 시작 (Start)", type="primary", use_container_width=True):
    
    if mode == "🏆 시가총액 상위":
        with st.spinner("기초 데이터 준비 중..."):
            # [수정] 캐싱된 함수 사용
            df_krx = get_stock_listing()
            # 이미 get_stock_listing에서 정렬과 ActualRank 생성이 완료되어 있음
            
            top_n = df_krx.head(st.session_state.stock_count)
            target_list = []
            
            # [수정] 필터링 로직 (리츠/인프라 등 제외)
            skipped_count = 0
            for i, (idx, row) in enumerate(top_n.iterrows()):
                name = row['Name']
                # 제외할 종목 리스트: S-RIM/EPS 분석이 맞지 않는 부동산/인프라 펀드 성격의 종목들
                if name in ["맥쿼리인프라", "SK리츠", "제이알글로벌리츠", "롯데리츠", "ESR켄달스퀘어리츠", "신한알파리츠", "맵스리얼티1", "이리츠코크렙", "코람코에너지리츠"]:
                    skipped_count += 1
                    continue
                
                # [수정] 인덱스(i+1) 대신 실제 순위(ActualRank) 사용
                rank_val = row['ActualRank'] if 'ActualRank' in row else i+1
                target_list.append((str(row['Code']), name, rank_val))
            
            if skipped_count > 0:
                st.toast(f"ℹ️ 리츠/인프라 종목 {skipped_count}개는 분석 특성상 자동 제외되었습니다.")
    
    if not target_list:
        st.warning("분석할 종목이 없습니다.")
        st.stop()

    status_box = st.empty()
    status_box.info("🇰🇷 금리 조회 & 멀티 프로세싱 준비...")
    
    bok_rate = get_bok_base_rate()
    applied_rate = bok_rate if bok_rate else 3.25
    
    status_box.success(f"✅ 기준금리 {applied_rate}% | {speed_option} 모드로 시작합니다...")
    time.sleep(0.5)
    
    p_bar = st.progress(0)
    # worker_count 파라미터 전달
    is_success = run_analysis_parallel(target_list, applied_rate, status_box, p_bar, worker_count)
    
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

    # [수정] 테이블 스타일: 강제 배경색 제거 (시스템 테마 자동 반영)
    def style_dataframe(row):
        styles = []
        for col in row.index:
            style = '' # 기본값은 빈 문자열 -> Streamlit 기본 테마 색상(자동) 적용
            
            if col == '괴리율':
                val = row['괴리율']
                if val > 20: 
                    # 저평가 -> 파스텔 레드
                    style = 'color: #D47C94; font-weight: bold;' 
                elif val < 0: 
                    # 고평가 -> 파스텔 블루 (흰 배경에서도 보이게 조금 더 진한 색 사용)
                    style = 'color: #5C7CFA; font-weight: bold;' 
            elif col == '공포지수':
                val = row['공포지수']
                if val <= 30: 
                    # 공포 -> 파스텔 레드
                    style = 'color: #D47C94; font-weight: bold;'
                elif val >= 70: 
                    # 탐욕 -> 파스텔 블루
                    style = 'color: #5C7CFA; font-weight: bold;'
            
            styles.append(style)
        return styles

    st.dataframe(
        df_display[cols].style.apply(style_dataframe, axis=1).format("{:,.0f}", subset=['현재가', '적정주가', 'EPS', 'BPS']),
        height=800,
        use_container_width=True
    )
else:
    st.info("👈 위에서 [분석 시작] 버튼을 눌러주세요.")
