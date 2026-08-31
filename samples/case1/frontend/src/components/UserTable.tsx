import { Check, Edit3, Trash2, UserRound, X } from "lucide-react";
import { useState } from "react";
import { getUploadUrl } from "../api";
import type { ApiClient, User } from "../types";

interface UserTableProps {
  api: ApiClient;
  users: User[];
  onChanged: () => Promise<void>;
}

export function UserTable({ api, users, onChanged }: UserTableProps) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftName, setDraftName] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const beginEdit = (user: User) => {
    setEditingId(user.id);
    setDraftName(user.name);
    setError("");
  };

  const saveEdit = async (id: number) => {
    if (!draftName.trim()) {
      setError("姓名不能为空");
      return;
    }
    setBusyId(id);
    try {
      const result = await api.updateUser(id, draftName.trim());
      if (!result.success) {
        setError(result.error || "保存失败");
        return;
      }
      setEditingId(null);
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusyId(null);
    }
  };

  const removeUser = async (user: User) => {
    if (!window.confirm(`确认删除用户“${user.name}”？`)) return;
    setBusyId(user.id);
    setError("");
    try {
      const result = await api.deleteUser(user.id);
      if (!result.success) {
        setError(result.error || "删除失败");
        return;
      }
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="panel panel--table" aria-label="已注册用户">
      <header className="panel__header">
        <div>
          <p className="eyebrow">IDENTITY STORE</p>
          <h2>已注册用户</h2>
        </div>
        <span className="count-badge">{users.length} 人</span>
      </header>
      {error ? <div className="notice notice--error" role="alert">{error}</div> : null}
      {users.length === 0 ? (
        <div className="empty-state"><UserRound size={27} aria-hidden="true" /><span>暂无注册用户</span></div>
      ) : (
        <div className="user-table-wrap">
          <table className="user-table">
            <thead>
              <tr><th scope="col">用户</th><th scope="col">创建时间</th><th scope="col">操作</th></tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const avatar = getUploadUrl(user.avatar);
                const editing = editingId === user.id;
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="user-cell">
                        {avatar ? <img src={avatar} alt={`${user.name}头像`} className="user-avatar" /> : <span className="user-avatar user-avatar--empty"><UserRound size={19} /></span>}
                        {editing ? (
                          <input aria-label={`编辑${user.name}`} className="inline-input" value={draftName} onChange={(event) => setDraftName(event.target.value)} />
                        ) : <strong>{user.name || "未命名"}</strong>}
                      </div>
                    </td>
                    <td className="muted-cell">{user.created_at || "--"}</td>
                    <td>
                      <div className="row-actions">
                        {editing ? (
                          <>
                            <button type="button" className="icon-button icon-button--success" aria-label="保存姓名" title="保存姓名" onClick={() => void saveEdit(user.id)} disabled={busyId === user.id}><Check size={17} /></button>
                            <button type="button" className="icon-button" aria-label="取消编辑" title="取消编辑" onClick={() => setEditingId(null)}><X size={17} /></button>
                          </>
                        ) : (
                          <button type="button" className="icon-button" aria-label={`编辑${user.name}`} title="编辑姓名" onClick={() => beginEdit(user)}><Edit3 size={17} /></button>
                        )}
                        <button type="button" className="icon-button icon-button--danger" aria-label={`删除${user.name}`} title="删除用户" onClick={() => void removeUser(user)} disabled={busyId === user.id}><Trash2 size={17} /></button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
