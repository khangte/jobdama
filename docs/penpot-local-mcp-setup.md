# 💻 Penpot 로컬 MCP 서버 설치 및 실행 가이드

> **주의:** 이 가이드는 시스템에 `pnpm` 패키지 관리자를 설치하는 과정과 `pnpm v11+` 환경에서 발생하는 빌드 차단 에러(`ERR_PNPM_IGNORED_BUILDS`)를 우회하는 명령어를 모두 포함하고 있습니다.
> 작업을 진행할 때 터미널 창을 **총 2개** 사용하게 됩니다.

---

### 0단계: [터미널 1] pnpm 설치하기

컴퓨터에 `pnpm`이 설치되어 있지 않다면, 전역(Global) 환경에 pnpm을 먼저 설치해 줍니다. 터미널을 열고 아래 명령어를 입력하세요.

```bash
npm install -g pnpm
```

- 설치가 완료되면 `pnpm -v` 명령어를 입력해 버전 번호가 정상적으로 출력되는지 확인합니다.

---

### 1단계: [터미널 1] AI 클라이언트에 로컬 주소 등록하기

사용 중인 AI 툴(Codex, Claude Desktop 등)에 Penpot MCP 서버 주소를 등록해 줍니다. 이어서 아래 명령어를 입력하세요.

```bash
npx -y add-mcp -g -n penpot http://localhost:4401/mcp
```

- 등록 완료 메시지가 뜨면 이 터미널 창은 닫으셔도 좋습니다.

---

### 2단계: [터미널 2] Penpot MCP 서버 본체 실행하기 (핵심)

pnpm 보안 차단을 우회하여 로컬 서버 엔진을 구동합니다. **새 터미널 창**을 열고 아래 명령어를 복사해서 입력하세요.

```bash
pnpm_config_dangerously_allow_all_builds=true npx -y @penpot/mcp@stable
```

- 💡 **중요:** 이 명령어를 입력하면 의존성 빌드가 진행된 후 서버가 대기 상태(`Server running...` 등)로 바뀝니다. **AI를 사용하는 동안 이 터미널 창은 절대 닫지 말고 그대로 켜두셔야 합니다.**

---

### 3단계: [웹 브라우저] Penpot에서 플러그인 연결하기

서버가 켜진 상태를 유지한 채, 디자인 작업을 할 브라우저로 이동합니다.

1. [Penpot 웹사이트](https://penpot.app)에 접속하여 본인의 디자인 파일을 엽니다.
2. 상단 메뉴에서 **Plugins ➔ Load from URL**을 클릭합니다.
3. 주소 입력창에 아래 URL을 정확히 입력하고 **설치(Install)** 버튼을 누릅니다.
   - `http://localhost:4400/manifest.json`
4. 플러그인 창이 열리면 **"Connect to MCP server"** 버튼을 클릭합니다.
5. 상태가 **"Connected"**로 바뀌면 모든 연결이 성공한 것입니다. (작업하는 동안 플러그인 창은 계속 열어두세요).

> ⚠️ **크롬 브라우저에서 연결 오류가 발생할 경우:**
> 새 탭에서 `chrome://flags/#allow-insecure-localhost` 주소로 이동한 뒤, **Allow insecure localhost** 항목을 **Enabled**로 변경하고 브라우저를 재시작하세요.

---

### 4단계: AI 툴에서 연동 테스트하기

이제 Cursor나 Claude 등의 AI 클라이언트 창을 열고 아래와 같이 프롬프트를 입력하여 연결을 확인합니다.

- `"현재 열려있는 Penpot 파일의 페이지 목록을 보여줘."`
- `"이 디자인 시스템의 컬러 스타일 구조를 분석해줘."`
