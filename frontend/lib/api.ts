import axios from "axios";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
export const AUTH_TOKEN_KEY = "fundscope_access_token";

export const api = axios.create({
  baseURL: API_BASE_URL || "",
});

api.interceptors.request.use((config) => {
  if (typeof window === "undefined") {
    return config;
  }
  const token = window.localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
