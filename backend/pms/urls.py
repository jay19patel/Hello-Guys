from django.urls import path

from pms import views

app_name = "pms"

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="projects"),
    path("new/", views.ProjectCreateView.as_view(), name="project-create"),
    path("<int:pk>/", views.ProjectDetailView.as_view(), name="project"),
    path("<int:pk>/tasks/new/", views.TaskCreateView.as_view(), name="task-create"),
    path("tasks/<int:pk>/", views.TaskDetailView.as_view(), name="task"),
    path("my-tasks/", views.MyTasksView.as_view(), name="my-tasks"),
]
