{{- define "omlorix.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "omlorix.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "omlorix.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "omlorix.labels" -}}
app.kubernetes.io/name: {{ include "omlorix.name" . }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "omlorix.selectorLabels" -}}
app.kubernetes.io/name: {{ include "omlorix.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "omlorix.localFileStorageClaimName" -}}
{{- default (printf "%s-user-files" (include "omlorix.fullname" .)) .Values.fileStorage.local.persistence.existingClaim -}}
{{- end -}}

{{- define "omlorix.localFileStorageUsesPersistence" -}}
{{- if and (eq (lower (toString .Values.env.FILE_STORAGE_PROVIDER)) "local") .Values.fileStorage.local.persistence.enabled -}}
true
{{- end -}}
{{- end -}}

{{- define "omlorix.localFileStorageVolumeMounts" -}}
{{- if include "omlorix.localFileStorageUsesPersistence" . -}}
volumeMounts:
  - name: user-files
    mountPath: {{ .Values.env.FILE_STORAGE_LOCAL_BASE_PATH | quote }}
{{- end -}}
{{- end -}}

{{- define "omlorix.localFileStorageVolumes" -}}
{{- if include "omlorix.localFileStorageUsesPersistence" . -}}
volumes:
  - name: user-files
    persistentVolumeClaim:
      claimName: {{ include "omlorix.localFileStorageClaimName" . }}
{{- end -}}
{{- end -}}

{{- define "omlorix.validateLocalFileStorage" -}}
{{- $provider := lower (toString (default "" .Values.env.FILE_STORAGE_PROVIDER)) -}}
{{- if eq $provider "local" -}}
{{- $path := toString (default "" .Values.env.FILE_STORAGE_LOCAL_BASE_PATH) -}}
{{- if not (hasPrefix "/" $path) -}}
{{- fail "env.FILE_STORAGE_LOCAL_BASE_PATH must be an absolute path when env.FILE_STORAGE_PROVIDER is local" -}}
{{- end -}}
{{- $persistenceEnabled := default false .Values.fileStorage.local.persistence.enabled -}}
{{- $allowEphemeral := default false .Values.fileStorage.local.allowEphemeral -}}
{{- if and (not $persistenceEnabled) (not $allowEphemeral) -}}
{{- fail "local file storage requires fileStorage.local.persistence.enabled=true, or set fileStorage.local.allowEphemeral=true for throwaway installs" -}}
{{- end -}}
{{- if $persistenceEnabled -}}
{{- $backendReplicas := int (default 1 .Values.api.replicas) -}}
{{- if .Values.worker.enabled -}}
{{- $backendReplicas = add $backendReplicas (int (default 1 .Values.worker.replicas)) -}}
{{- end -}}
{{- if .Values.scheduler.enabled -}}
{{- $backendReplicas = add $backendReplicas (int (default 1 .Values.scheduler.replicas)) -}}
{{- end -}}
{{- if gt $backendReplicas 1 -}}
{{- $accessModes := default (list) .Values.fileStorage.local.persistence.accessModes -}}
{{- if not (has "ReadWriteMany" $accessModes) -}}
{{- fail "local file storage shared by multiple API/worker/scheduler pods requires fileStorage.local.persistence.accessModes to include ReadWriteMany" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "omlorix.validateValues" -}}
{{- if .Values.validation.enabled -}}
{{- $errors := list -}}
{{- $imageRepository := default "" .Values.image.repository | trim -}}
{{- $frontendImageRepository := default "" .Values.frontendImage.repository | trim -}}
{{- $existingSecretName := default "" .Values.existingSecretName | trim -}}
{{- $env := .Values.env -}}
{{- $envSecret := .Values.envSecret -}}
{{- $publicUrl := default "" (get $env "PUBLIC_URL") | trim -}}
{{- if or (not $imageRepository) (hasPrefix "ghcr.io/example/" $imageRepository) -}}
{{- $errors = append $errors "Set image.repository to the published Omlorix backend image or to your private backend image." -}}
{{- end -}}
{{- if and .Values.frontend.enabled (or (not $frontendImageRepository) (hasPrefix "ghcr.io/example/" $frontendImageRepository)) -}}
{{- $errors = append $errors "Set frontendImage.repository to the published Omlorix frontend image or to your private frontend image." -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not $publicUrl) -}}
{{- $errors = append $errors "Set env.PUBLIC_URL to the public Kubernetes ingress origin when ingress.enabled is true." -}}
{{- end -}}
{{- if not $existingSecretName -}}
{{- $jwtSecret := default "" (get $envSecret "JWT_SECRET_KEY") | trim -}}
{{- $encryptionKey := default "" (get $envSecret "ENCRYPTION_KEY") | trim -}}
{{- $databaseUrl := default "" (get $envSecret "DATABASE_URL") | trim -}}
{{- $databaseHost := default "" (get $env "DATABASE_HOST") | trim -}}
{{- $databaseName := default "" (get $env "DATABASE_NAME") | trim -}}
{{- $databaseUser := default "" (get $envSecret "DATABASE_USER") | trim -}}
{{- $databasePassword := default "" (get $envSecret "DATABASE_PASSWORD") | trim -}}
{{- if lt (len $jwtSecret) 32 -}}
{{- $errors = append $errors "Set envSecret.JWT_SECRET_KEY to a secret value at least 32 characters long, or set existingSecretName to a Secret that provides JWT_SECRET_KEY." -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9_-]{43}=$" $encryptionKey) -}}
{{- $errors = append $errors "Set envSecret.ENCRYPTION_KEY to a valid Fernet key, or set existingSecretName to a Secret that provides ENCRYPTION_KEY." -}}
{{- end -}}
{{- if and (not $databaseUrl) (or (not $databaseHost) (not $databaseName) (not $databaseUser) (not $databasePassword)) -}}
{{- $errors = append $errors "Configure the database with envSecret.DATABASE_URL, or set env.DATABASE_HOST, env.DATABASE_NAME, envSecret.DATABASE_USER, and envSecret.DATABASE_PASSWORD. You can also set existingSecretName to a Secret that provides DATABASE_URL." -}}
{{- end -}}
{{- end -}}
{{- if gt (len $errors) 0 -}}
{{- fail (printf "Omlorix Helm values are incomplete:\n- %s" (join "\n- " $errors)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
