{{- define "s2.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "s2.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "s2.labels" -}}
app.kubernetes.io/name: {{ include "s2.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "s2.selectorLabels" -}}
app.kubernetes.io/name: {{ include "s2.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: ontology-engine
{{- end -}}

{{- define "s2.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{/* Environment shared by the API and the migration job, minus the database URL. */}}
{{- define "s2.commonEnv" -}}
- name: S2_ENV
  value: production
- name: S2_LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: S2_OIDC_ISSUER
  value: {{ .Values.oidc.issuer | quote }}
- name: S2_OIDC_JWKS_URL
  value: {{ .Values.oidc.jwksUrl | quote }}
- name: S2_OIDC_AUDIENCE
  value: {{ .Values.oidc.audience | quote }}
- name: S2_OPA_URL
  value: {{ .Values.opa.url | quote }}
{{- end -}}
