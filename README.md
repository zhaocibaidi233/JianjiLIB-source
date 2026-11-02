# 简籍（Jianji）

面向中小学图书馆的编目、馆藏、流通、读者服务与统计分析一体化系统。

本仓库是简籍的源码仓库，包含后端、前端、Windows 启动器和最小内置数据。可直接下载使用的 Windows 便携部署包见 `JianjiLIB` 仓库的 Release。

## 组件

| 目录 | 内容 |
| --- | --- |
| `backend/` | FastAPI + SQLAlchemy 2 + Alembic |
| `frontend/` | Vue 3 + TypeScript + Vite |
| `launcher/` | Windows 启动器（.NET Framework WinForms） |
| `docs/` | 部署与运维文档 |

## 构建

运行环境：Python 3.13、Node.js 20+、PostgreSQL 15+。

后端：

```bat
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe scripts\init_db.py
```

前端：

```bat
cd frontend
npm ci
npm run build
```

启动器：

```bat
cd launcher
build-launcher.bat
```

构建完成后，`frontend/dist` 由后端以单端口方式托管。开发模式可使用 `npm run dev`，但不要将 Vite 开发服务器直接对外提供服务。

## 数据

仓库内置最小数据：22 个中图法一级大类、常用 CNMARC 字段和基础字典。数据来源与使用边界见 [`DATA_NOTICE.md`](DATA_NOTICE.md)。

## 部署包

Windows 便携包（内置 Python 与 PostgreSQL）发布在 [`JianjiLIB`](https://github.com/zhaocibaidi233/JianjiLIB) 的 Releases 页面。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](LICENSE) 发布。通过网络向用户提供服务时，须按 AGPL 提供对应源代码。

第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
