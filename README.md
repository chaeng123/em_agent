# Emergency_Agent (EMAgent) — MCP 서버

응급 질환 발생 시 응급실과 연계하는 MCP Agent.
PlayMCP in KC (카카오 클라우드) 공모전 배포용.

## 제공 도구 (Tools)

| Tool | 기능 |
|------|------|
| `assess_urgency` | 증상 입력 → KTAS 기반 **참고용** 응급도 추정 + 안전 안내 |
| `find_emergency_room` | 시도/시군구 입력 → 실시간 응급실 가용병상 조회 (공공데이터 API) |
| `first_aid_guidance` | 상황별 일반인 대상 표준 응급처치 안내 |

## 안전 설계 원칙

- 모든 응답에 "참고용이며 실제 판정·처치는 의료진 우선" 안내 포함
- 레드플래그(의식저하·호흡곤란·흉통 등) 감지 시 즉시 119 안내
- 의학적 진단·확정 분류·전문 중재 지시를 하지 않음

---

## 사전 준비: 공공데이터 API 키 발급

1. [공공데이터포털](https://www.data.go.kr/data/15000563/openapi.do) 접속·로그인
2. "국립중앙의료원_전국 응급의료기관 정보 조회 서비스" **활용신청** (개발계정 자동승인)
3. 발급된 **일반 인증키(serviceKey)** 를 복사
4. 배포 시 환경변수 `DATA_GO_KR_SERVICE_KEY` 에 등록

---

## 배포 방법 A — Git 소스 등록 (권장)

1. 이 폴더를 GitHub 저장소에 푸시
   ```bash
   git init
   git add .
   git commit -m "Emergency_Agent MCP server"
   git remote add origin https://github.com/<your-id>/emergency-agent.git
   git push -u origin main
   ```
2. PlayMCP in KC → "Git 소스로 MCP 서버 등록하기" 선택
3. 저장소 URL 입력, 환경변수 `DATA_GO_KR_SERVICE_KEY` 설정
4. 빌드·배포 완료 후 발급된 **Endpoint URL** 확인

## 배포 방법 B — 컨테이너 이미지 등록

1. 이미지 빌드 & 푸시
   ```bash
   docker build -t <registry>/<team>/emergency-agent:v1.0.0 .
   docker push <registry>/<team>/emergency-agent:v1.0.0
   ```
2. PlayMCP in KC "컨테이너 이미지" 등록 폼 입력:
   - Registry 호스트: 레지스트리 주소
   - image_name: `<team>/emergency-agent`
   - image_tag: `v1.0.0`
   - 환경변수 `DATA_GO_KR_SERVICE_KEY` 설정

---

## MCP Endpoint URL

배포 후 엔드포인트는 다음 형태입니다:

```
https://<배포주소>/mcp
```

이 URL을 PlayMCP 서버 등록 화면의 **MCP Endpoint URL** 에 입력하면 공모전 참여가 완료됩니다.

## 로컬 테스트

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_키"
python server.py
# → http://localhost:8000/mcp 에서 동작
```
