import { createRouter, createWebHistory } from "vue-router";
import Home from "./views/Home.vue";
import Dashboard from "./views/Dashboard.vue";
import Summary from "./views/Summary.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Home },
    { path: "/trip/:id", component: Dashboard },
    { path: "/trip/:id/summary", component: Summary },
  ],
});
