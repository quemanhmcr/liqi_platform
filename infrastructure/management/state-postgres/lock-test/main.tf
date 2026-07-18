variable "hold_seconds" {
  type    = number
  default = 20
}

resource "terraform_data" "lock_holder" {
  triggers_replace = timestamp()

  provisioner "local-exec" {
    command = "${path.module}/hold-lock.sh ${var.hold_seconds}"
  }
}
