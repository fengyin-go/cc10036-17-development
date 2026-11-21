import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import LineageExchangeView from '@/views/LineageExchangeView.vue'
import SchedulerDetailView from '@/views/SchedulerDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/data_lineage/exchange', name: 'lineage-exchange', component: LineageExchangeView },
    { path: '/query_scheduler/detail', name: 'scheduler-detail-default', component: SchedulerDetailView },
    { path: '/query_scheduler/:planId(\\d+)', name: 'scheduler-detail', component: SchedulerDetailView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
