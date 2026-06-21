import jmcomic

# 列出 JmOption 的所有可配置字段
opt = jmcomic.JmOption()
print("JmOption 可用配置字段:")
print([attr for attr in dir(opt) if not attr.startswith('_')])
