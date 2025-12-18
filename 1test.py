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
my_password = "1478"

st.set_page_config(page_title="KOSPI 분석기", page_icon="🎨", layout="wide")

password_input = st.text_input("비밀번호를 입력하세요", type="password")

if password_input != my_password:
    st.error("비밀번호를 입력하고 엔터를 누르면 실행됩니다.")
    st.stop()

st.write("🎉 Made By 찬용")

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
    if 'Marcap' in df.columns:
        df = df.sort_values(by='Marcap', ascending=False)
        df['ActualRank'] = range(1, len(df) + 1)
        # 주식수 계산 (시가총액 / 현재가) - 데이터가 없을 경우를 대비
        df['Shares'] = df.apply(lambda x: x['Marcap'] / x['Close'] if x['Close'] > 0 else 0, axis=1)
    else:
        df['ActualRank'] = 0
        df['Shares'] = 0
    return df

# --- [수정된 핵심 로직] 적정주가 산출 함수 ---
def calculate_target_price(eps, bps, total_debt, total_equity, shares):
    """
    요청사항 1: EPS*10 + BPS
    단, 부채비율(총부채/총자본) > 100% 인 경우:
    (EPS*10 + BPS) - (총부채 - 총자본) / 주식수
    * total_debt, total_equity 단위: 억원 -> 원으로 변환 필요 (1억 = 100,000,000)
    """
    if shares <= 0: return 0
    
    # 기본 적정가
    base_price = (eps * 10) + bps
    
    # 부채비율 체크
    if total_equity > 0:
        debt_ratio = (total_debt / total_equity) * 100
        if debt_ratio > 100:
            # 초과 부채에 대한 페널티 계산
            # 데이터 크롤링 단위가 '억원'이므로 1억을 곱해줌
            excess_debt_value = (total_debt - total_equity) * 100000000
            penalty_per_share = excess_debt_value / shares
            
            final_price = base_price - penalty_per_share
            return final_price
    
    return base_price

# --- 개별 종목 데이터 크롤링 ---
def fetch_stock_data(item):
    code, name, rank, shares = item
    
    # 결과 저장용 변수
    prev_eps, prev_bps = 0.0, 0.0
    est_eps, est_bps = 0.0, 0.0
    
    prev_debt, prev_equity = 0.0, 0.0 # 직전년도
    latest_debt, latest_equity = 0.0, 0.0 # 최신 분기 (연간예상치 대체용)
    
    current_price = 0.0
    
    try:
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

        # 재무제표 파싱
        dfs = pd.read_html(res.text, encoding='cp949')
        
        for df in dfs:
            # 주요재무제표 테이블 찾기 (매출액, 영업이익 등이 포함된 표)
            if '매출액' in df.iloc[:, 0].to_string() or '영업이익' in df.iloc[:, 0].to_string():
                
                # 컬럼 정리 (날짜)
                # 보통 최근 연간 실적 3~4개 + 최근 분기 실적 6개 정도가 나옴
                # 예: 2022.12 | 2023.12 | 2024.12(E) | ...
                
                # MultiIndex 처리
                if isinstance(df.columns, pd.MultiIndex):
                    cols = [str(c[1]) for c in df.columns] # 두번째 레벨이 날짜
                else:
                    cols = [str(c) for c in df.columns]
                
                # 데이터 행 찾기
                # 첫번째 컬럼을 인덱스로 설정하여 찾기 쉽게 변환
                df = df.set_index(df.columns[0])
                
                # 1. 직전년도 데이터 찾기 (예: 2024년이면 2023년 결산)
                # (E)가 없고 가장 최근인 연도 컬럼 찾기
                annual_cols = [c for c in cols if 'E' not in c and re.match(r'\d{4}\.\d{2}', c)]
                # 분기 데이터 제외 (보통 연간 데이터가 앞에 나옴. 단순화를 위해 앞에서부터 검색)
                # 네이버 금융은 [연간] [분기] 섹션이 나눠져 있진 않고 쭉 나열됨.
                # 보통 앞쪽 3~4개가 연간.
                
                # 직전년도 컬럼 인덱스 찾기 (가장 오른쪽의 확정 연도)
                prev_col = None
                for c in cols:
                    if re.match(r'\d{4}\.\d{2}', c) and '(E)' not in c:
                        prev_col = c # 계속 갱신하면 마지막 확정 연도가 됨 (분기 제외 로직 필요하지만 일단 간단히)
                        # 주의: 네이버 표는 연간 4개, 분기 6개 순서임.
                        # 연도(YYYY.MM) 포맷인 것 중 앞쪽 4개 안에서 찾아야 함.
                
                # 안전하게: 컬럼명 리스트에서 '(E)'가 있는 첫번째 컬럼의 바로 앞 컬럼을 직전년도로 간주
                # 또는 (E)가 없으면 전체 중 가장 최근 연간
                
                est_col_idx = -1
                for i, c in enumerate(cols):
                    if '(E)' in c:
                        est_col_idx = i
                        break
                
                if est_col_idx != -1:
                    target_est_col = cols[est_col_idx]
                    target_prev_col = cols[est_col_idx - 1] # 예상치 바로 앞이 직전 확정치
                else:
                    # 예상치가 없으면 그냥 가장 최근 확정치 사용
                    # 연간 섹션(보통 인덱스 1~4) 중 마지막
                    # 인덱스 0은 항목명.
                    target_est_col = None # 예상치 없음
                    # 날짜 형식인 컬럼 중 분기가 아닌 것 찾기 애매하므로, 
                    # 통상적으로 3번째 데이터 컬럼(최근)을 사용
                    if len(cols) > 3:
                        target_prev_col = cols[3] 
                    else:
                        target_prev_col = cols[-1]

                # --- 데이터 추출 함수 ---
                def get_val(idx_name):
                    # 인덱스 이름에 포함된 행 찾기
                    found = df.index[df.index.str.contains(idx_name, na=False)]
                    if len(found) > 0:
                        return found[0]
                    return None

                # 1) 과년도(직전년도) 데이터 추출
                if target_prev_col:
                    try:
                        prev_eps = to_float(df.loc[get_val('EPS'), target_prev_col])
                        prev_bps = to_float(df.loc[get_val('BPS'), target_prev_col])
                        prev_debt = to_float(df.loc[get_val('부채총계'), target_prev_col])
                        prev_equity = to_float(df.loc[get_val('자본총계'), target_prev_col])
                    except: pass

                # 2) 연간 예상치(Estimate) 데이터 추출
                if target_est_col:
                    try:
                        est_eps = to_float(df.loc[get_val('EPS'), target_est_col])
                        est_bps = to_float(df.loc[get_val('BPS'), target_est_col])
                        est_debt = to_float(df.loc[get_val('부채총계'), target_est_col])
                        est_equity = to_float(df.loc[get_val('자본총계'), target_est_col])
                    except: pass
                else:
                    # 예상치 없으면 직전년도 데이터를 예상치로 사용 (보수적 접근)
                    est_eps, est_bps = prev_eps, prev_bps
                
                # 3) 최신 분기 데이터 (부채/자본 누락 대비용)
                # 보통 테이블의 가장 오른쪽 끝이 최신 분기일 확률 높음 (네이버 구조상)
                last_col = cols[-1]
                try:
                    latest_debt = to_float(df.loc[get_val('부채총계'), last_col])
                    latest_equity = to_float(df.loc[get_val('자본총계'), last_col])
                except: pass
                
                break # 표를 찾았으니 루프 종료

        # --- 적정주가 계산 ---
        # 1. 과년도 적정주가 (직전년도 실적 + 직전년도 부채비율)
        fair_price_prev = calculate_target_price(prev_eps, prev_bps, prev_debt, prev_equity, shares)
        
        # 2. 목표 적정주가 (예상치 실적 + 부채비율)
        # 단, 예상치에 부채/자본 데이터가 0이면 최신 분기 데이터 사용 (요청사항 3)
        calc_debt = est_debt if est_debt > 0 else latest_debt
        calc_equity = est_equity if est_equity > 0 else latest_equity
        
        fair_price_target = calculate_target_price(est_eps, est_bps, calc_debt, calc_equity, shares)

        # 3. Gap (괴리율) : 목표 적정주가 대비 현재가
        gap = 0
        if current_price > 0:
            gap = (fair_price_target - current_price) / current_price * 100
        
        # 4. Diff (현재가 - 과년도 적정주가) : 요청사항 5 정렬용
        diff_prev = current_price - fair_price_prev

        return {
            'code': code, 'name': name, 'rank': rank,
            'price': current_price,
            'fair_prev': fair_price_prev,   # 과년도 적정주가
            'fair_target': fair_price_target, # 목표(예상) 적정주가
            'gap': gap,
            'diff_prev': diff_prev
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
            
            if data and data['price'] > 0: # 현재가가 0인 거래정지 종목 등 제외
                status_text.text(f"⚡ [{completed_count}/{total}] {data['name']} 분석 완료")
                
                results.append({
                    '종목코드': data['code'],
                    '종목명': data['name'],
                    '시총순위': data['rank'],
                    '과년도 적정주가': round(data['fair_prev'], 0),
                    '현재가': round(data['price'], 0),
                    '적정주가': round(data['fair_target'], 0), # 이것이 목표 적정가
                    '괴리율(%)': round(data['gap'], 2),
                    'Gap_Prev': data['diff_prev'] # 정렬용 히든 컬럼
                })

    progress_bar.empty()
    if results:
        st.session_state['analysis_result'] = pd.DataFrame(results)
        return True
    return False

# --- 메인 UI ---
st.markdown("<div class='responsive-header'>⚖️ KOSPI 분석기 2.0Ver</div>", unsafe_allow_html=True)

# 1. 설명서
with st.expander("📘 **공지사항 & 산출공식**", expanded=True):
    st.markdown("""
    <div class='info-text'>
    <span class='pastel-blue'>산출공식 (부채비율 반영)</span><br>
    <b>1. 기본 공식 (부채비율 100% 이하)</b><br>
    &nbsp; • 적정주가 = <b>(EPS × 10) + BPS</b><br><br>
    
    <b>2. 부채 과다 페널티 (부채비율 100% 초과)</b><br>
    &nbsp; • 적정주가 = (EPS × 10) + BPS - <b>[(총부채 - 총자본) ÷ 주식수]</b><br>
    &nbsp; <span class='pastel-red'>* 초과된 부채만큼 주당 가치를 차감하여 보수적으로 산정합니다.</span><br><br>

    <span class='pastel-blue'>데이터 기준</span><br>
    &nbsp; • <b>과년도 적정주가:</b> 직전년도 확정 실적 기준<br>
    &nbsp; • <b>적정주가 (Target):</b> 네이버 연간 예상치(컨센서스) 기준<br>
    &nbsp; (※ 예상치 부채정보 부재 시 최신 분기 데이터 사용)
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

mode = st.radio("분석 모드", ["🏆 시가총액 상위", "🔍 종목 검색"], horizontal=True)
target_list = [] 

if mode == "🏆 시가총액 상위":
    if 'stock_count' not in st.session_state: st.session_state.stock_count = 200 

    def update_from_slider(): st.session_state.stock_count = st.session_state.slider_key
    def apply_manual_input(): st.session_state.stock_count = st.session_state.num_input

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
                df_krx = get_stock_listing()
                res = df_krx[df_krx['Name'].str.contains(query, case=False)]
                if res.empty: st.error("결과 없음")
                else:
                    picks = st.multiselect("선택", res['Name'].tolist(), default=res['Name'].tolist()[:5])
                    selected = res[res['Name'].isin(picks)]
                    for idx, row in selected.iterrows():
                        rank_val = row['ActualRank'] if 'ActualRank' in row else 0
                        shares = row['Shares'] if 'Shares' in row else 0
                        target_list.append((str(row['Code']), row['Name'], rank_val, shares))
        except: st.error("오류 발생")

# --- 2. 실행 ---
st.divider()
if st.button("▶️ 분석 시작 (Start)", type="primary", use_container_width=True):
    
    if mode == "🏆 시가총액 상위":
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

sort_opt = st.radio("정렬 기준", ["괴리율 높은 순 (저평가)", "📉 현재가-과년도적정가 작은 순 (진성저평가)"], horizontal=True)

if st.button("🔄 결과 새로고침"): st.rerun()

if 'analysis_result' in st.session_state and not st.session_state['analysis_result'].empty:
    df = st.session_state['analysis_result']
    
    # 정렬 로직 수정
    if "괴리율" in sort_opt:
        df = df.sort_values(by='괴리율(%)', ascending=False)
    else:
        # 현재가 - 과년도 적정가 (작을수록 과년도 가치 대비 현재가가 싼 것)
        df = df.sort_values(by='Gap_Prev', ascending=True)
    
    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "순위"
    
    # 표시할 컬럼 지정 (요청사항 4)
    # 순위(Index) | 종목명 | 과년도 적정주가 | 현재가 | 적정주가(목표) | 괴리율
    # Gap_Prev는 정렬용이므로 표시 안 함
    cols = ['시총순위', '과년도 적정주가', '현재가', '적정주가', '괴리율(%)']
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
        df_display[cols].style.apply(style_dataframe, axis=1).format("{:,.0f}", subset=['과년도 적정주가', '현재가', '적정주가']),
        height=800,
        use_container_width=True
    )
else:
    st.info("👈 위에서 [분석 시작] 버튼을 눌러주세요.")
