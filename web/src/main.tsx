import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/plus-jakarta-sans/800.css";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./styles.css";
import "./screens.css";
import "./theme.css";  
import { RequireAuth } from "./auth";
import { getToken } from "./api";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Forgot from "./pages/Forgot";
import Trips from "./pages/Trips";
import Join from "./pages/Join";
import TripRoom from "./pages/TripRoom";
import Preferences from "./pages/Preferences";
import Places from "./pages/Places";
import Plan from "./pages/Plan";


const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
const guard = (el: React.ReactNode) => <RequireAuth>{el}</RequireAuth>;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <div className="app">
          <Routes>
            <Route path="/" element={<Navigate to={getToken() ? "/trips" : "/login"} replace />} />
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/forgot" element={<Forgot />} />
            <Route path="/trips" element={guard(<Trips />)} />
            <Route path="/join/:code" element={guard(<Join />)} />
            <Route path="/trips/:id" element={guard(<TripRoom />)} />
            <Route path="/trips/:id/preferences" element={guard(<Preferences />)} />
            <Route path="/trips/:id/places" element={guard(<Places />)} />
            <Route path="/trips/:id/plan" element={guard(<Plan />)} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
