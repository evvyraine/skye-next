import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { MotionConfig } from "motion/react"
import { registerSW } from "virtual:pwa-register"
import { SoundProvider, ToastProvider } from "sunkit-ui"

import "./index.css"
import App from "./App.tsx"
import { OpsApp } from "@/ops/ops-app.tsx"
import { ThemeProvider } from "@/components/theme-provider.tsx"

registerSW({ immediate: true })

const isOps = window.location.pathname.startsWith("/ops")

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="system">
      <SoundProvider volume={0.55}>
        <MotionConfig reducedMotion="user">
          <ToastProvider>{isOps ? <OpsApp /> : <App />}</ToastProvider>
        </MotionConfig>
      </SoundProvider>
    </ThemeProvider>
  </StrictMode>
)
