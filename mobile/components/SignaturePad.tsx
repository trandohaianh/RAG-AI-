import React, { useMemo, useRef, useState } from "react";
import { PanResponder, StyleSheet, View, type LayoutChangeEvent } from "react-native";
import Svg, { Path } from "react-native-svg";
import type { SignatureStroke } from "../lib/api";
import { colors } from "../lib/theme";
import { addSignaturePoint, strokeToPath } from "../lib/signature";

type Props = {
  strokes: SignatureStroke[];
  onChange: (strokes: SignatureStroke[]) => void;
  /** Báo cho màn cha khoá cuộn trong lúc khách đang ký. */
  onSigningChange?: (signing: boolean) => void;
};

/**
 * Khung ký bằng ngón tay (M12). Chỉ ghi lại nét ký đã chuẩn hoá 0..1 theo khung 3:1;
 * máy chủ tự vẽ thành ảnh chữ ký nên không cần thư viện chụp ảnh khung.
 */
export default function SignaturePad({ strokes, onChange, onSigningChange }: Props) {
  const [size, setSize] = useState({ width: 1, height: 1 });
  // PanResponder được tạo một lần; dùng ref để luôn đọc giá trị mới nhất.
  const latest = useRef({ onChange, onSigningChange, size });
  latest.current = { onChange, onSigningChange, size };
  // Nét ký giữ trong ref và cập nhật ngay từng sự kiện: ngón tay có thể gửi nhiều điểm
  // trước khi màn hình kịp vẽ lại, nếu đọc từ props sẽ mất điểm / mất nét.
  const work = useRef(strokes);
  const emitted = useRef(strokes);
  if (strokes !== emitted.current) {
    // Màn cha đổi nét từ bên ngoài (ví dụ "Ký lại").
    work.current = strokes;
    emitted.current = strokes;
  }
  function emit(next: SignatureStroke[]) {
    work.current = next;
    emitted.current = next;
    latest.current.onChange(next);
  }

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (event) => {
          const { onSigningChange: signing, size: box } = latest.current;
          signing?.(true);
          const { locationX, locationY } = event.nativeEvent;
          emit([...work.current, addSignaturePoint([], locationX, locationY, box.width, box.height)]);
        },
        onPanResponderMove: (event) => {
          const { size: box } = latest.current;
          const current = work.current;
          if (current.length === 0) return;
          const { locationX, locationY } = event.nativeEvent;
          const last = current[current.length - 1];
          const next = addSignaturePoint(last, locationX, locationY, box.width, box.height);
          if (next !== last) emit([...current.slice(0, -1), next]);
        },
        onPanResponderRelease: () => latest.current.onSigningChange?.(false),
        onPanResponderTerminate: () => latest.current.onSigningChange?.(false),
      }),
    []
  );

  function onLayout(event: LayoutChangeEvent) {
    const { width, height } = event.nativeEvent.layout;
    setSize({ width: Math.max(width, 1), height: Math.max(height, 1) });
  }

  return (
    <View
      style={styles.pad}
      onLayout={onLayout}
      accessibilityLabel="Khung ký của khách hàng"
      {...responder.panHandlers}
    >
      <Svg width="100%" height="100%" viewBox="0 0 900 300" pointerEvents="none">
        {strokes.map((stroke, index) => (
          <Path
            key={index}
            d={strokeToPath(stroke, 900, 300)}
            stroke="#111827"
            strokeWidth={5}
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        ))}
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  pad: {
    width: "100%",
    aspectRatio: 3,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderStyle: "dashed",
    borderColor: colors.border,
    borderRadius: 10,
    overflow: "hidden",
  },
});
