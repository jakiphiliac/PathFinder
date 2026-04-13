import { createRouter, createWebHistory } from "vue-router";
import Home from "./views/Home.vue";
import Dashboard from "./views/Dashboard.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Home },
    { path: "/trip/:id", component: Dashboard },
  ],
});
