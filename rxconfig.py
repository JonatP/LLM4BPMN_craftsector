import reflex as rx

config = rx.Config(
    app_name="LLM4BPMN_reflex",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ]
)