# Health_Navigator — MCP 서버

질환 발생 시 인턴 초진 양식을 결합해 **예상 질환과 담당 진료과**를 안내하고,
**근처 병·의원**을 찾아주며, **병원 방문 전 자가 처치**를 질병관리청 국가건강정보포털과
연계(딥링크)해 제공하는 MCP Agent. PlayMCP in KC(카카오클라우드) 공모전 배포용.

> ℹ️ 일반적인 건강정보 안내용 **참고 도구**이며, 의학적 확정 진단이 아닙니다.
> 증상이 심하거나 호전되지 않으면 반드시 의료기관에서 진료를 받으세요.

## 제공 도구 (Tools)

| Tool | 기능 |
|------|------|
| `structure_intake` | 초진 기록(주호소·증상·과거력 등) → 표준 초진 양식(SOAP)으로 정리 |
| `predict_conditions` | 초진 소견 → 예상 질환(감별) + **담당 진료과** + 권장 진료 시급성 |
| `find_nearby_hospital` | 시도/시군구 + 진료과 → 근처 **병·의원** 조회 (공공데이터 API) |
| `self_care_guidance` | 질환/증상 → 병원 방문 전 **자가 처치** + 국가건강정보포털 공식 링크 |

각 도구는 MCP 표준 `annotations`(title / readOnlyHint / destructiveHint /
idempotentHint / openWorldHint)를 정의하고, description에 서비스명(`Health_Navigator`)을
포함합니다.

## 일반적 사용 흐름

```
structure_intake        (초진 기록 정리)
   → predict_conditions    (예상 질환 + 담당 진료과)
      → find_nearby_hospital   (해당 진료과의 근처 병원)
      → self_care_guidance     (병원 가기 전 자가 처치 + 질병관리청 링크)
```

## 안전 설계 원칙

- 모든 응답에 "참고용이며 진단·처치는 의료진 우선" 안내 포함
- 위급 신호(의식저하·호흡곤란·갑작스러운 마비/언어장애·심한 출혈·경련 등) 감지 시
  예측보다 우선해 **즉시 119/응급실** 안내
- 의학적 확정 진단·전문 중재 지시를 하지 않음
- 자가 처치 안내는 국가건강정보포털 공개정보를 참고한 일반 수준이며,
  공식 콘텐츠 원문은 포털 링크(출처표시·비영리·변경금지)로 제공

---

## 사전 준비: 공공데이터 API 키 발급

병원 찾기에는 국립중앙의료원 제공 공공데이터를 사용합니다.

1. [공공데이터포털](https://www.data.go.kr/data/15000736/openapi.do) 접속·로그인
2. **"국립중앙의료원_전국 병·의원 찾기 서비스"** 활용신청 (개발계정 자동승인)
3. 발급된 **일반 인증키(Decoding serviceKey)** 복사
4. 배포 시 환경변수 `DATA_GO_KR_SERVICE_KEY` 에 등록

> 동일 제공기관(B552657)이므로 기존 응급실/AED 키를 그대로 재사용할 수 있습니다.
> 키는 코드에 하드코딩하지 말고 **환경변수로만** 주입하세요.

### (선택) 질병관리청 국가건강정보포털 연계

`self_care_guidance`는 질환별 국가건강정보포털 공식 링크를 출처표시로 제공합니다.
국가건강정보포털 API는 **LINK형(출처표시·비영리·변경금지)** 이라 별도 REST 키가 필요하지
않으며, 콘텐츠 API를 직접 연동하려면 [health.kdca.go.kr 건강정보 신청](https://health.kdca.go.kr/healthinfo/biz/health/portalUseGuidance/hlthinsReqst/hlthinsReqstMth.do)
승인이 필요합니다.

---

## 배포 (카카오클라우드 Git 소스 빌드)

1. 이 폴더(`server.py`, `kb.py`, `requirements.txt`, `Dockerfile`)를 GitHub에 push
   ```bash
   ./push.sh        # 또는 git init/add/commit/push
   ```
2. 카카오클라우드 "Git 소스 빌드"에서 설정
   - **Git URL**: `https://github.com/<your-id>/em_agent.git`
   - **브랜치/ref**: `main`
   - **Dockerfile 경로**: `Dockerfile`
   - **PAT**: private 저장소면 GitHub Personal Access Token(Contents: Read) 입력
3. 환경변수 `DATA_GO_KR_SERVICE_KEY` 설정 후 빌드/배포
4. 발급된 **Endpoint URL**(`https://<배포주소>/mcp`) 확인
5. PlayMCP 콘솔에서 Endpoint 입력 → `정보 불러오기`로 4개 툴 확인 → `등록 및 심사 요청`

> 코드를 수정할 때마다 카카오클라우드에서 **재빌드**해야 반영됩니다(push만으로는 자동 반영 안 됨).

## 로컬 테스트

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_키"
python server.py
# → http://localhost:8000/mcp 에서 동작
```

## 파일 구성

| 파일 | 역할 |
|------|------|
| `server.py` | MCP 서버 본체 (FastMCP, 4개 툴, streamable-http) |
| `kb.py` | 내장 지식베이스 (질환·진료과·자가처치·진료과목 코드) |
| `requirements.txt` | 의존성 (`mcp`, `httpx`) |
| `Dockerfile` | 컨테이너 빌드 정의 (`server.py`, `kb.py` 포함) |
| `push.sh` | GitHub push 헬퍼 스크립트 |
