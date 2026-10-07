// 서비스 앱의 CardFace 를 이 도구에서 쓰도록 감싼다. build.mjs 가 팀 프런트 저장소를 빌드 입력으로 쓴다.
import { createRoot } from "react-dom/client";
import { CardFace } from "@team/features/card/CardFace";

const roots = new WeakMap();

window.MotionCardFace = {
  render(element, card) {
    let root = roots.get(element);
    if (!root) {
      root = createRoot(element);
      roots.set(element, root);
    }
    root.render(<CardFace card={card} width="100%" />);
  },
};
