import router from './router'
import { bootstrapApp } from '../bootstrap'
import {
  ElBacktop, ElButton, ElCol, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDivider, ElFooter, ElForm, ElFormItem, ElHeader, ElIcon,
  ElInput, ElMain, ElMenu, ElMenuItem, ElOption, ElPageHeader,
  ElPagination, ElPopover, ElRate, ElRow, ElSelect, ElStatistic,
  ElSubMenu, ElTable, ElTableColumn, ElTag, ElWatermark,
} from 'element-plus'
import {
  CircleCheck, CircleClose, EditPen, Hide, InfoFilled, Minus, Search, Upload, View,
} from '@element-plus/icons-vue'

const components = [
  ElBacktop, ElButton, ElCol, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDivider, ElFooter, ElForm, ElFormItem, ElHeader, ElIcon,
  ElInput, ElMain, ElMenu, ElMenuItem, ElOption, ElPageHeader,
  ElPagination, ElPopover, ElRate, ElRow, ElSelect, ElStatistic,
  ElSubMenu, ElTable, ElTableColumn, ElTag, ElWatermark,
]

bootstrapApp(router, (app) => {
  for (const component of components) app.use(component)
}, (app) => {
  for (const [name, icon] of Object.entries({ CircleCheck, CircleClose, EditPen, Hide, InfoFilled, Minus, Search, Upload, View })) {
    app.component(name, icon)
  }
})
