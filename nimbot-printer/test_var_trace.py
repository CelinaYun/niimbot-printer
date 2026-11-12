"""回归测试：控件销毁后，变量的 write trace 不能再打到死控件上。

对应故障：切语言 / 切主题重建界面后，旧 StringVar 被回收时 tkinter 会
globalunsetvar，Tcl 的 unset 同样触发 write trace，回调里 winfo_width()
抛 TclError: bad window path name "....!segmented"。
"""

import gc
import tkinter as tk

import fluent as fl


def _collect_errors(root: tk.Tk) -> list[str]:
    errors: list[str] = []

    def hook(exc, val, tb):  # noqa: ANN001
        errors.append(f"{exc.__name__}: {val}")

    root.report_callback_exception = hook
    return errors


def _pump(root: tk.Tk, times: int = 5) -> None:
    for _ in range(times):
        root.update_idletasks()
        root.update()


def case_destroyed_segmented(root: tk.Tk) -> None:
    """控件销毁后变量再被写入：不许抛异常（原来的崩法）。"""
    var = tk.StringVar(value="a")
    seg = fl.Segmented(root, ["a", "b"], var)
    seg.pack()
    _pump(root)

    seg.destroy()
    del seg
    var.set("b")     # 旧写法在这里 TclError: bad window path name
    _pump(root)
    var.set("a")
    _pump(root)


def case_destroyed_entry_and_toggle(root: tk.Tk) -> None:
    var = tk.StringVar()
    entry = fl.FluentEntry(root, textvariable=var)
    entry.pack()
    on = tk.BooleanVar(value=False)
    tog = fl.Toggle(root, on)
    tog.pack()
    _pump(root)

    entry.destroy()
    tog.destroy()
    del entry, tog
    var.set("晚到的写入")
    on.set(True)
    del var, on
    gc.collect()
    _pump(root)


def case_pending_after(root: tk.Tk) -> None:
    """after(20) 的首帧重画排在队列里，控件先死了也不能炸。"""
    var = tk.StringVar(value="x")
    seg = fl.Segmented(root, ["x", "y"], var)
    seg.pack()
    seg.destroy()          # 不等 20ms 就销毁
    _pump(root, times=8)


def case_rebuild_like_gui(root: tk.Tk) -> None:
    """模拟 gui._rebuild_ui：销毁容器、重建同名控件，旧变量随后仍被写入。"""
    holder = tk.Frame(root)
    holder.pack()
    var = tk.StringVar(value="ZH")
    fl.Segmented(holder, ["ZH", "EN"], var).pack()
    _pump(root)

    holder.destroy()       # 等价于 self.topbar.destroy()
    gc.collect()

    holder2 = tk.Frame(root)
    holder2.pack()
    var2 = tk.StringVar(value="EN")
    fl.Segmented(holder2, ["ZH", "EN"], var2).pack()
    var2.set("ZH")         # 新控件正常刷新
    var.set("EN")          # 旧变量被写入：旧控件早已销毁
    _pump(root)
    holder2.destroy()


def main() -> int:
    root = tk.Tk()
    root.geometry("320x200")
    errors = _collect_errors(root)

    for case in (case_destroyed_segmented, case_destroyed_entry_and_toggle,
                 case_pending_after, case_rebuild_like_gui):
        before = len(errors)
        case(root)
        if len(errors) > before:
            print(f"[FAIL] {case.__name__}")
            for e in errors[before:]:
                print("       ", e)
        else:
            print(f"[ OK ] {case.__name__}")

    root.destroy()
    if errors:
        print(f"\n共 {len(errors)} 个回调异常")
        return 1
    print("\nvar trace ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
