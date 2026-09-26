import "./globals.css";

export const metadata = {
  title: "justfastllm Dashboard",
  description: "Operational dashboard for justfastllm proxy metrics, virtual keys, pricing, speed, tokens, spend, and guardrails."
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
