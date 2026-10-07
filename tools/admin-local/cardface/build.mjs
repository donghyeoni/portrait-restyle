// 완성 카드 미리보기 모듈을 만든다. 서비스 프런트(팀 저장소)의 CardFace 를 그대로 묶는다.
//   node tools/admin-local/cardface/build.mjs
// local.json 의 teamFrontend(팀 저장소의 frontend 폴더)와 그 안의 node_modules 를 쓴다.
// 결과(static/cardface/)는 팀 코드라 공개 저장소에 올리지 않는다(.gitignore).
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(readFileSync(join(here, "..", "local.json"), "utf8"));
if (!config.teamFrontend) {
  console.error("local.json 에 teamFrontend(팀 저장소 frontend 폴더 경로)를 넣으세요.");
  process.exit(1);
}
const team = resolve(config.teamFrontend);
const modules = join(team, "node_modules");
const { build } = await import(pathToFileURL(join(modules, "vite", "dist", "node", "index.js")).href);

await build({
  configFile: false,
  root: team,
  publicDir: false,
  logLevel: "warn",
  esbuild: { jsx: "automatic" },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  resolve: {
    alias: [
      { find: "@team", replacement: join(team, "src") },
      // 입력 파일이 팀 저장소 밖에 있어 react 를 찾지 못한다. 팀 저장소의 것을 쓴다.
      { find: /^react-dom(\/.*)?$/, replacement: join(modules, "react-dom") + "$1" },
      { find: /^react(\/.*)?$/, replacement: join(modules, "react") + "$1" },
    ],
  },
  build: {
    outDir: join(here, "..", "static", "cardface"),
    emptyOutDir: true,
    cssCodeSplit: false,
    lib: { entry: join(here, "entry.jsx"), formats: ["es"], fileName: () => "cardface.js" },
    rollupOptions: { output: { assetFileNames: "cardface[extname]" } },
  },
});
console.log("완성 카드 미리보기 모듈을 만들었습니다: static/cardface/");
