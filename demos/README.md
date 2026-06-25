# Demos

此目录用于集中存放可视化 demo 和未来备选方案。

## 目录约定

- 每个方案使用一个独立子目录。
- 子目录入口文件统一命名为 `index.html`。
- 方案相关页面、样式和静态资源应留在对应子目录内。
- `output/trace_*` 继续作为运行产物和优化路径归档，不作为人工审阅 demo 的主入口。

## 当前方案

- `prompt-evolver-visualization/`：提示词优化路径可视化方案集合。
- `git-diff-viewer/`：类似 PyCharm Git diff 的左右并列审阅页面，支持新增、删除、修改色块和同步滚动。
- `prompt-diff-viewer/`：用于对比两个 Markdown prompt 的左右并列 diff 页面，支持 CLI 默认加载、本地文件导入和复制粘贴。
