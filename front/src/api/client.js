import axios from "axios";
import { getToken, logout } from "../auth/auth";
import router from "../router";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/",
});

// Ajoute le token à chaque requête
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Déconnexion auto si le token est invalide/expiré
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      logout();
      router.push("/login");
    }
    return Promise.reject(error);
  }
);

export default api;