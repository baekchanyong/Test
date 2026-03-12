import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import time
import requests
import re
from datetime import datetime, timedelta
import concurrent.futures

# --- [비밀번호 설정 구간 시작] ---
my_password = "1414"

st.set_page_config(page_title="KOSPI 분석기", page_icon="🎨", layout="wide")

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
    .pastel-blue { color: #5C7CFA; font-weight: bold; }
    .pastel-red { color: #D47C94; font-weight: bold; }
    
    /* 버튼 스타일 조정 */
    .stButton button {
        width: 100%;
        border-radius: 5px;
        height: 3em;
    }
    
    @media (max-width: 600px) { .info-text { font-size: 0.9rem; } }
</style>
""", unsafe_allow_html=True)

# --- 헬퍼 함수 ---
def to_float(val):
    try:
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        clean_val = re.sub(r'[(),%]', '', str(val))
        return float(clean_val)
    except: return 0.0

# --- 종목 리스트 로딩 ---
@st.cache_data
def get_stock_listing():
    df = fdr.StockListing('KOSPI')
    if 'Symbol' in df.columns:
        df = df.rename(columns={'Symbol': 'Code'})
    
    # 데이터 타입 강제 변환
    if 'Close' in df.columns:
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce').fillna(0)
    if 'Marcap' in df.columns:
        df['Marcap'] = pd.to_numeric(df['Marcap'], errors='coerce').fillna(0)

    if 'Marcap' in df.columns:
        df = df.sort_values(by='Marcap', ascending=False)
        df['ActualRank'] = range(1, len(df) + 1)
        # 주식수 계산
        df['Shares'] = np.where(df['Close'] > 0, df['Marcap'] / df['Close'], 0)
    else:
        df['ActualRank'] = 0
        df['Shares'] = 0
    return df

# --- 적정주가 산출 로직 (부채 반영) ---
def calculate_fair_value_v2(eps, bps, debt_total, equity_total, shares):
    """
    공식: EPS * 10 + BPS
    단, 부채비율(부채/자본) > 100% 인 경우:
      (EPS * 10 + BPS) - (총부채 - 총자본) / 주식수
    """
    if shares <= 0: return 0
    
    # 기본 적정가
    base_price = (eps * 10) + bps
    
    # 부채비율 확인
    if equity_total > 0:
        debt_ratio = (debt_total / equity_total) * 100
        if debt_ratio > 100:
            # 초과 부채 (억원 단위 -> 원 단위 변환: * 1억)
            excess_debt = (debt_total - equity_total) * 100000000
            penalty = excess_debt / shares
            final_price = base_price - penalty
            return final_price
            
    return base_price

# --- 개별 종목 데이터 크롤링 ---
def fetch_stock_data(item):
    code, name, rank, shares = item
    
    # 초기화
    current_price = 0.0
    
    # 과년도(직전년도) 데이터
    prev_eps, prev_bps, prev_debt, prev_equity = 0.0, 0.0, 0.0, 0.0
    
    # 목표(예상치) 데이터
    target_eps, target_bps, target_debt, target_equity = 0.0, 0.0, 0.0, 0.0
    
    # 최신 분기 데이터 (예상치 자본 누락 시 대체용)
    quarter_debt, quarter_equity = 0.0, 0.0
    
    # [추가] 유동부채 데이터 저장을 위한 변수
    quarter_current_debt = 0.0
    
    try:
        # 1차 크롤링: 네이버 금융 메인 (일반 재무제표)
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.naver.com/'
        }
        res = requests.get(url, headers=headers, timeout=5)
        
        # 현재가 파싱
        try:
             match = re.search(r'blind">\s*([0-9,]+)\s*<', res.text)
             if match: current_price = to_float(match.group(1))
        except: pass

        dfs = pd.read_html(res.text, encoding='cp949')
        
        # 재무제표 찾기
        for df in dfs:
            if '매출액' in df.iloc[:, 0].to_string() or '영업이익' in df.iloc[:, 0].to_string():
                
                # 인덱스 정리
                df = df.set_index(df.columns[0])
                
                # 컬럼명 리스트
                if isinstance(df.columns, pd.MultiIndex):
                     cols = [str(c[1]) for c in df.columns]
                else:
                     cols = [str(c) for c in df.columns]
                
                # (E)가 있는 연간 컬럼 찾기
                est_idx = -1
                for i, c in enumerate(cols):
                    if '(E)' in c and re.search(r'\d{4}\.\d{2}', c):
                        est_idx = i
                        break
                
                # 직전년도(확정) 인덱스 찾기
                prev_idx = -1
                if est_idx != -1:
                    prev_idx = est_idx - 1
                else:
                    for i in range(len(cols)-1, -1, -1):
                        if re.match(r'\d{4}\.\d{2}', cols[i]) and '(E)' not in cols[i]:
                            if i < 4: 
                                prev_idx = i
                                break
                    if prev_idx == -1: prev_idx = 3 

                quarter_idx = len(cols) - 1

                # 데이터 추출 헬퍼
                def get_data(row_name, col_idx):
                    if col_idx < 0 or col_idx >= len(cols): return 0.0
                    try:
                        target_rows = df.index[df.index.str.contains(row_name, na=False)]
                        if len(target_rows) > 0:
                            return to_float(df.iloc[df.index.get_loc(target_rows[0]), col_idx])
                    except: pass
                    return 0.0

                # 1) 과년도 데이터 추출
                prev_eps = get_data('EPS', prev_idx)
                prev_bps = get_data('BPS', prev_idx)
                prev_debt = get_data('부채총계', prev_idx)
                prev_equity = get_data('자본총계', prev_idx)
                
                # 2) 목표(예상) 데이터 추출
                target_idx = est_idx if est_idx != -1 else prev_idx
                target_eps = get_data('EPS', target_idx)
                target_bps = get_data('BPS', target_idx)
                target_debt = get_data('부채총계', target_idx)
                target_equity = get_data('자본총계', target_idx)
                
                # 3) 최신 분기 데이터
                quarter_debt = get_data('부채총계', quarter_idx)
                quarter_equity = get_data('자본총계', quarter_idx)
                
                break

        # [추가] 2차 크롤링: FnGuide 상세 재무상태표에서 '유동부채' 콕 집어오기
        try:
            fg_url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}"
            fg_res = requests.get(fg_url, headers=headers, timeout=5)
            fg_dfs = pd.read_html(fg_res.text)
            
            for fg_df in fg_dfs:
                if '유동부채' in fg_df.iloc[:, 0].to_string():
                    fg_df = fg_df.set_index(fg_df.columns[0])
                    target_rows = fg_df.index[fg_df.index.str.contains('유동부채', na=False)]
                    if len(target_rows) > 0:
                        row_data = fg_df.loc[target_rows[0]]
                        # 표의 가장 오른쪽(최신 데이터)부터 역순으로 탐색하여 0이 아닌 값 추출
                        for val in reversed(row_data.values):
                            c_val = to_float(val)
                            if c_val > 0:
                                quarter_current_debt = c_val
                                break
                    break
        except Exception as e:
            pass

        # --- 적정주가 산출 ---
        # 1. 과년도 적정주가
        fair_prev = calculate_fair_value_v2(prev_eps, prev_bps, prev_debt, prev_equity, shares)
        
        # 2. 목표 적정주가 (부채 페널티 로직)
        use_debt = target_debt if target_debt > 0 else quarter_debt
        use_equity = target_equity if target_equity > 0 else quarter_equity
        
        fair_target = calculate_fair_value_v2(target_eps, target_bps, use_debt, use_equity, shares)
        
        # 3. [추가] 목표주가 산출: BPS + 10*예상EPS - (유동부채 / 주식수)
        # FnGuide 데이터 단위가 '억원'이므로 1억을 곱해 원 단위로 환산 후 주식수로 나눔
        target_price_val = 0
        if shares > 0:
            penalty_current_debt = (quarter_current_debt * 100000000) / shares
            target_price_val = target_bps + (10 * target_eps) - penalty_current_debt
        else:
            target_price_val = target_bps + (10 * target_eps)

        # 괴리율 (목표 적정가 기준)
        gap = 0
        if current_price > 0:
            gap = (fair_target - current_price) / current_price * 100
            
        # 정렬용
        diff_val = current_price - fair_prev

        return {
            'code': code, 'name': name, 'rank': rank,
            'price': current_price,
            'fair_prev': fair_prev, 'fair_target': fair_target,
            'target_price': target_price_val,
            'gap': gap, 'diff_val': diff_val
        }

    except Exception as e:
        return None

# --- 분석 실행 (병렬) ---
def run_analysis_parallel(target_list, status_text, progress_bar, worker_count):
    results = []
    total = len(target_list)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fetch_stock_data, item): item for item in target_list}
        
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            data = future.result()
            completed_count += 1
            progress_bar.progress(min(completed_count / total, 1.0))
            
            if data and data['price'] > 0:
                status_text.text(f"⚡ [{completed_count}/{total}] {data['name']} 분석 완료")
                
                results.append({
                    '종목코드': data['code'],
                    '종목명': data['name'],
                    '시총순위': data['rank'],
                    '과년도 적정주가': round(data['fair_prev'], 0),
                    '현재가': round(data['price'], 0),
                    '적정주가': round(data['fair_target'], 0),
                    '목표주가': round(data['target_price'], 0),
                    '괴리율(%)': round(data['gap'], 2),
                    'Gap_Prev': data['diff_val']
                })

    progress_bar.empty()
    if results:
        st.session_state['analysis_result'] = pd.DataFrame(results)
        return True
    return False

# --- 메인 UI ---
st.markdown("<div class='responsive-header'>⚖️ KOSPI 분석기 1.1Ver</div>", unsafe_allow_html=True)

# 1. 설명서
with st.expander("📘 **공지사항 & 산출공식**", expanded=True):
    st.markdown("""
    <div class='info-text'>
    <span class='pastel-blue'>산출공식 (부채비율 반영)</span><br>
    <b>1. 기본 공식 (부채비율 100% 이하)</b><br>
    &nbsp; • 적정주가 = <b>(EPS × 10) + BPS</b><br><br>
    
    <b>2. 부채 과다 페널티 (부채비율 100% 초과)</b><br>
    &nbsp; • 적정주가 = (EPS × 10) + BPS - <b>[(총부채 - 총자본) ÷ 주식수]</b><br>

    <span class='pastel-blue'>데이터 기준</span><br>
    &nbsp; • <b>과년도 적정주가:</b> 직전년도 확정 실적 기준<br>
    &nbsp; • <b>적정주가 (Target):</b> 네이버 연간 예상치(컨센서스) 기준<br>
    &nbsp; (※ 예상치 부채정보 부재 시 최신 분기 데이터 사용)
    </div>
    """, unsafe_allow_html=True)

# 2. 패치노트
with st.expander("🛠️ **패치노트**", expanded=False):
    st.markdown("""
    <div class='info-text'>
    
    <b>(25.11.26) 1.0Ver : 최초배포</b><br>
    &nbsp; • 분석 제외종목 : 맥쿼리인프라, SK리츠, 제이알글로벌리츠, 롯데리츠, ESR켄달스퀘어리츠, 신한알파리츠, 맵스리얼티1, 이리츠코크렙, 코람코에너지리츠<br>
    &nbsp;   - 일반제조업과 회계방식차이로 인하여 과도하게 저평가되는 종목들 제외<br>
    &nbsp; • 시총순위 : ETF(KODEX200 등) 제외한 시가총액 순위<br>
      
    <b>(25.11.26) 1.1Ver : 적정주가 산출방식 변경</b><br>
    &nbsp; • 적정주가 수식 변경<br>
    &nbsp;   - 공포지수, eps,bps반영율 변경<br>
    &nbsp; • 종목 검색기능 추가<br>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- 1. 설정 ---
st.header("1. 분석 설정")

speed_option = st.radio(
    "분석 속도 설정",
    ["빠른 분석 (15개씩)", "보통 분석 (8개씩)", "느린 분석 (2개씩)"],
    index=1
)
worker_count = 15 if "빠른" in speed_option else (8 if "보통" in speed_option else 2)

st.divider()

# 분석 모드 선택
mode = st.radio("분석 모드 선택", ["🏆 시가총액 상위 종목 분석", "🔍 특정 종목 검색/추천 분석"], horizontal=True)

target_list = [] 

if mode == "🏆 시가총액 상위 종목 분석":
    st.write("📊 **분석할 상위 종목 수 설정**")
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 200 

    def update_from_slider(): st.session_state.stock_count = st.session_state.slider_key
    def apply_manual_input(): st.session_state.stock_count = st.session_state.num_input

    c1, c2 = st.columns([3, 1])
    with c1:
        st.slider("종목 수 조절", 10, 400, key='slider_key', value=st.session_state.stock_count, on_change=update_from_slider)
    with c2:
        st.number_input("직접 입력", 10, 400, key='num_key', value=st.session_state.stock_count)
        if st.button("✅ 수치 적용", on_click=apply_manual_input): st.rerun()

elif mode == "🔍 특정 종목 검색/추천 분석":
    if 'search_basket' not in st.session_state:
        st.session_state.search_basket = []

    search_query = st.text_input("종목명 검색", placeholder="예: 삼성, 현대, 카카오")
    
    # [UI 수정] 그리드 형태의 검색 결과
    if search_query:
        try:
            df_krx = get_stock_listing()
            search_results = df_krx[df_krx['Name'].str.contains(search_query, case=False)]
            
            if search_results.empty:
                st.error(f"❌ '{search_query}' 검색 결과가 없습니다.")
            else:
                st.write(f"🔎 검색 결과: {len(search_results)}건")
                display_limit = 18 # 3의 배수로 설정
                results_to_show = search_results.head(display_limit)
                
                # 3열 그리드로 표시
                cols = st.columns(3)
                for idx, row in results_to_show.iterrows():
                    # 인덱스에 따라 0, 1, 2 컬럼에 배분
                    col_idx = list(results_to_show.index).index(idx) % 3
                    
                    with cols[col_idx]:
                        with st.container():
                            # 버튼을 왼쪽에 배치하기 위해 컬럼 분할
                            # c_btn(0.25) | c_info(0.75)
                            c_btn, c_info = st.columns([0.25, 0.75])
                            
                            is_added = any(d['code'] == str(row['Code']) for d in st.session_state.search_basket)
                            
                            with c_btn:
                                if is_added:
                                    st.button("✅", key=f"added_{row['Code']}", disabled=True)
                                else:
                                    if st.button("➕", key=f"add_{row['Code']}"):
                                        st.session_state.search_basket.append({
                                            'code': str(row['Code']),
                                            'name': row['Name'],
                                            'rank': row['ActualRank'] if 'ActualRank' in row else 0,
                                            'shares': row['Shares'] if 'Shares' in row else 0
                                        })
                                        st.rerun()
                            
                            with c_info:
                                st.markdown(f"**{row['Name']}**")
                                st.caption(f"{row['Code']}")
                                
                        st.markdown("---") # 구분선

                if len(search_results) > display_limit:
                    st.caption(f"외 {len(search_results)-display_limit}건 생략... (검색어를 더 구체적으로 입력하세요)")

        except Exception as e:
            st.error(f"검색 중 오류 발생: {e}")

    # 현재 대기 목록 보여주기
    st.markdown("### 📋 분석 대기 목록")
    
    if len(st.session_state.search_basket) > 0:
        basket_df = pd.DataFrame(st.session_state.search_basket)
        st.dataframe(basket_df[['code', 'name', 'rank']], hide_index=True, use_container_width=True)
        
        if st.button("🗑️ 목록 초기화", type="secondary"):
            st.session_state.search_basket = []
            st.rerun()
    else:
        st.info("위 검색창에서 종목을 찾아 [➕] 버튼을 눌러주세요.")

# --- 2. 실행 ---
st.divider()
if st.button("▶️ 분석 시작 (Start)", type="primary", use_container_width=True):
    
    # 1. 상위 종목 모드
    if mode == "🏆 시가총액 상위 종목 분석":
        with st.spinner("기초 데이터 준비 중..."):
            df_krx = get_stock_listing()
            top_n = df_krx.head(st.session_state.stock_count)
            target_list = []
            
            skipped_count = 0
            for i, (idx, row) in enumerate(top_n.iterrows()):
                name = row['Name']
                if name in ["맥쿼리인프라", "SK리츠", "제이알글로벌리츠", "롯데리츠", "ESR켄달스퀘어리츠", "신한알파리츠", "맵스리얼티1", "이리츠코크렙", "코람코에너지리츠"]:
                    skipped_count += 1
                    continue
                
                rank_val = row['ActualRank'] if 'ActualRank' in row else i+1
                shares = row['Shares'] if 'Shares' in row else 0
                target_list.append((str(row['Code']), name, rank_val, shares))
            
            if skipped_count > 0:
                st.toast(f"ℹ️ 리츠/인프라 종목 {skipped_count}개 자동 제외됨")
    
    # 2. 검색 모드
    else:
        if not st.session_state.search_basket:
            st.warning("분석할 종목을 먼저 검색해서 담아주세요.")
            st.stop()
        
        target_list = [
            (item['code'], item['name'], item['rank'], item['shares']) 
            for item in st.session_state.search_basket
        ]

    if not target_list:
        st.warning("분석할 종목이 없습니다.")
        st.stop()

    status_box = st.empty()
    p_bar = st.progress(0)
    
    is_success = run_analysis_parallel(target_list, status_box, p_bar, worker_count)
    
    if is_success:
        status_box.success(f"✅ 분석 완료!")
        time.sleep(0.5)
        st.rerun()

# --- 3. 결과 ---
st.divider()
st.header("🏆 분석 결과")

sort_opt = st.radio("정렬 기준", ["괴리율 높은 순 (저평가)", "📉 저평가 심화 순 (현재가-과년도적정가)"], horizontal=True)

if st.button("🔄 결과 새로고침"): st.rerun()

if 'analysis_result' in st.session_state and not st.session_state['analysis_result'].empty:
    df = st.session_state['analysis_result']
    
    # 정렬 로직
    if "괴리율" in sort_opt:
        df = df.sort_values(by='괴리율(%)', ascending=False)
    else:
        df = df.sort_values(by='Gap_Prev', ascending=True)
    
    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "순위"
    
    cols = ['시총순위', '과년도 적정주가', '현재가', '적정주가', '목표주가', '괴리율(%)']
    df_display = df.set_index('종목명', append=True)
    
    top = df.iloc[0]
    st.info(f"🥇 **1위: {top['종목명']}** (시총 {top['시총순위']}위) | 괴리율: {top['괴리율(%)']}%")

    def style_dataframe(row):
        styles = []
        for col in row.index:
            style = '' 
            if col == '괴리율(%)':
                val = row['괴리율(%)']
                if val > 20: style = 'color: #D47C94; font-weight: bold;' 
                elif val < 0: style = 'color: #5C7CFA; font-weight: bold;' 
            styles.append(style)
        return styles

    st.dataframe(
        df_display[cols].style.apply(style_dataframe, axis=1).format("{:,.0f}", subset=['과년도 적정주가', '현재가', '적정주가', '목표주가']),
        height=800,
        use_container_width=True
    )
else:
    st.info("👈 위에서 [분석 시작] 버튼을 눌러주세요.")
