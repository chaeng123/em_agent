#!/usr/bin/env bash
# =============================================================================
# Emergency_Agent → GitHub push 스크립트
#
# 사용법 (Mac 터미널에서):
#   cd ~/Documents/playmcp/em_agent-main
#   chmod +x push.sh
#   ./push.sh
#
# [중요] 이 스크립트는 git 히스토리를 새로 만들어 force-push 합니다.
#        => 예전에 노출됐던 API 키가 들어있는 기존 히스토리를 통째로 덮어써서
#           제거하는 효과가 있습니다. (의도된 동작)
#        => 단, 키 자체는 공공데이터포털에서 반드시 폐기/재발급 하세요.
# =============================================================================
set -e   # 오류 나면 즉시 중단

REPO_URL="https://github.com/chaeng123/em_agent.git"
BRANCH="main"
COMMIT_MSG="${1:-fix: tool annotations + service name, 4-tool 구조 + 내장 KB, 키 하드코딩 제거}"

# 0) 현재 폴더가 맞는지 확인
if [ ! -f "server.py" ]; then
  echo "❌ server.py가 없습니다. em_agent-main 폴더 안에서 실행하세요."
  exit 1
fi

# 1) git 저장소 초기화 (이미 있으면 건너뜀)
if [ ! -d ".git" ]; then
  echo "▶ git 저장소 초기화"
  git init
fi

# 2) 브랜치 이름을 main 으로
git branch -M "$BRANCH"

# 3) 원격(origin) 연결 (이미 있으면 URL만 갱신)
if git remote | grep -q "^origin$"; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi

# 4) 변경사항 스테이징 + 커밋
git add -A
git commit -m "$COMMIT_MSG"

# 5) 푸시 (기존 히스토리 덮어쓰기 = 노출 키 제거)
echo "▶ GitHub로 push (force) ..."
git push -u origin "$BRANCH" --force

echo ""
echo "✅ 완료: https://github.com/chaeng123/em_agent"
echo "   다음 → 카카오클라우드 재배포 + 환경변수 DATA_GO_KR_SERVICE_KEY 설정 → PlayMCP 심사 요청"
